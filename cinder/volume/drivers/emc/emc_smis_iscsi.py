# Copyright (c) 2012 - 2014 EMC Corporation.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""
ISCSI Drivers for EMC VNX and VMAX arrays based on SMI-S.

"""


from cinder import context
from cinder import exception
from cinder.openstack.common.gettextutils import _
from cinder.openstack.common import log as logging
from cinder.volume import driver
from cinder.volume.drivers.emc import emc_smis_common

LOG = logging.getLogger(__name__)


class EMCSMISISCSIDriver(driver.ISCSIDriver):
    """EMC ISCSI Drivers for VMAX and VNX using SMI-S.

    Version history:
        1.0.0 - Initial driver
        1.1.0 - Multiple pools and thick/thin provisioning,
                performance enhancement.
    """

    VERSION = "1.1.0"

    def __init__(self, *args, **kwargs):

        super(EMCSMISISCSIDriver, self).__init__(*args, **kwargs)
        self.common =\
            emc_smis_common.EMCSMISCommon('iSCSI',
                                          configuration=self.configuration)

    def check_for_setup_error(self):
        pass

    def create_volume(self, volume):
        """Creates a EMC(VMAX/VNX) volume."""
        volpath = self.common.create_volume(volume)

        model_update = {}
        volume['provider_location'] = str(volpath)
        model_update['provider_location'] = volume['provider_location']
        return model_update

    def create_volume_from_snapshot(self, volume, snapshot):
        """Creates a volume from a snapshot."""
        volpath = self.common.create_volume_from_snapshot(volume, snapshot)

        model_update = {}
        volume['provider_location'] = str(volpath)
        model_update['provider_location'] = volume['provider_location']
        return model_update

    def create_cloned_volume(self, volume, src_vref):
        """Creates a cloned volume."""
        volpath = self.common.create_cloned_volume(volume, src_vref)

        model_update = {}
        volume['provider_location'] = str(volpath)
        model_update['provider_location'] = volume['provider_location']
        return model_update

    def delete_volume(self, volume):
        """Deletes an EMC volume."""
        self.common.delete_volume(volume)

    def create_snapshot(self, snapshot):
        """Creates a snapshot."""
        ctxt = context.get_admin_context()
        volumename = snapshot['volume_name']
        index = volumename.index('-')
        volumeid = volumename[index + 1:]
        volume = self.db.volume_get(ctxt, volumeid)

        volpath = self.common.create_snapshot(snapshot, volume)

        model_update = {}
        snapshot['provider_location'] = str(volpath)
        model_update['provider_location'] = snapshot['provider_location']
        return model_update

    def delete_snapshot(self, snapshot):
        """Deletes a snapshot."""
        ctxt = context.get_admin_context()
        volumename = snapshot['volume_name']
        index = volumename.index('-')
        volumeid = volumename[index + 1:]
        volume = self.db.volume_get(ctxt, volumeid)

        self.common.delete_snapshot(snapshot, volume)

    def ensure_export(self, context, volume):
        """Driver entry point to get the export info for an existing volume."""
        pass

    def create_export(self, context, volume):
        """Driver entry point to get the export info for a new volume."""
        pass

    def remove_export(self, context, volume):
        """Driver entry point to remove an export for a volume."""
        pass

    def check_for_export(self, context, volume_id):
        """Make sure volume is exported."""
        pass

    def initialize_connection(self, volume, connector):
        """Initializes the connection and returns connection info.

        The iscsi driver returns a driver_volume_type of 'iscsi'.
        the format of the driver data is defined in smis_get_iscsi_properties.
        Example return value::

            {
                'driver_volume_type': 'iscsi'
                'data': {
                    'target_discovered': True,
                    'target_iqn': 'iqn.2010-10.org.openstack:volume-00000001',
                    'target_portal': '127.0.0.0.1:3260',
                    'volume_id': '12345678-1234-4321-1234-123456789012',
                }
            }

        """
        self.common.initialize_connection(volume, connector)

        iscsi_properties = self.smis_get_iscsi_properties(volume, connector)
        return {
            'driver_volume_type': 'iscsi',
            'data': iscsi_properties
        }

    def _do_iscsi_discovery(self, volume):

        LOG.warn(_("ISCSI provider_location not stored, using discovery"))

        (out, _err) = self._execute('iscsiadm', '-m', 'discovery',
                                    '-t', 'sendtargets', '-p',
                                    self.configuration.iscsi_ip_address,
                                    run_as_root=True)
        targets = []
        for target in out.splitlines():
            targets.append(target)

        return targets

    def smis_get_iscsi_properties(self, volume, connector):
        """Gets iscsi configuration.

        We ideally get saved information in the volume entity, but fall back
        to discovery if need be. Discovery may be completely removed in future
        The properties are:

        :target_discovered:    boolean indicating whether discovery was used

        :target_iqn:    the IQN of the iSCSI target

        :target_portal:    the portal of the iSCSI target

        :target_lun:    the lun of the iSCSI target

        :volume_id:    the UUID of the volume

        :auth_method:, :auth_username:, :auth_password:

            the authentication details. Right now, either auth_method is not
            present meaning no authentication, or auth_method == `CHAP`
            meaning use CHAP with the specified credentials.
        """
        properties = {}

        location = self._do_iscsi_discovery(volume)
        if not location:
            raise exception.InvalidVolume(_("Could not find iSCSI export "
                                          " for volume %s") %
                                          (volume['name']))

        LOG.debug("ISCSI Discovery: Found %s" % (location))
        properties['target_discovered'] = True

        device_info = self.common.find_device_number(volume, connector)
        if device_info is None or device_info['hostlunid'] is None:
            exception_message = (_("Cannot find device number for volume %s")
                                 % volume['name'])
            raise exception.VolumeBackendAPIException(data=exception_message)

        device_number = device_info['hostlunid']
        storage_system = device_info['storagesystem']

        # sp is "SP_A" or "SP_B"
        sp = device_info['owningsp']
        endpoints = []
        if sp:
            # endpoints example:
            # [iqn.1992-04.com.emc:cx.apm00123907237.a8,
            # iqn.1992-04.com.emc:cx.apm00123907237.a9]
            endpoints = self.common._find_iscsi_protocol_endpoints(
                sp, storage_system)

        foundEndpoint = False
        for loc in location:
            results = loc.split(" ")
            properties['target_portal'] = results[0].split(",")[0]
            properties['target_iqn'] = results[1]
            # owning sp is None for VMAX
            # for VNX, find the target_iqn that matches the endpoint
            # target_iqn example: iqn.1992-04.com.emc:cx.apm00123907237.a8
            # or iqn.1992-04.com.emc:cx.apm00123907237.b8
            if not sp:
                break
            for endpoint in endpoints:
                if properties['target_iqn'] == endpoint:
                    LOG.debug("Found iSCSI endpoint: %s" % endpoint)
                    foundEndpoint = True
                    break
            if foundEndpoint:
                break

        if sp and not foundEndpoint:
            LOG.warn(_("ISCSI endpoint not found for SP %(sp)s on "
                     "storage system %(storage)s.")
                     % {'sp': sp,
                        'storage': storage_system})

        properties['target_lun'] = device_number

        properties['volume_id'] = volume['id']

        LOG.debug("ISCSI properties: %s" % (properties))

        auth = volume['provider_auth']
        if auth:
            (auth_method, auth_username, auth_secret) = auth.split()

            properties['auth_method'] = auth_method
            properties['auth_username'] = auth_username
            properties['auth_password'] = auth_secret

        return properties

    def terminate_connection(self, volume, connector, **kwargs):
        """Disallow connection from connector."""
        self.common.terminate_connection(volume, connector)

    def extend_volume(self, volume, new_size):
        """Extend an existing volume."""
        self.common.extend_volume(volume, new_size)

    def get_volume_stats(self, refresh=False):
        """Get volume stats.

        If 'refresh' is True, run update the stats first.
        """
        if refresh:
            self.update_volume_stats()

        return self._stats

    def update_volume_stats(self):
        """Retrieve stats info from volume group."""
        LOG.debug("Updating volume stats")
        data = self.common.update_volume_stats()
        backend_name = self.configuration.safe_get('volume_backend_name')
        data['volume_backend_name'] = backend_name or 'EMCSMISISCSIDriver'
        data['storage_protocol'] = 'iSCSI'
        data['driver_version'] = self.VERSION
        self._stats = data
