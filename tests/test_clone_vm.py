# Copyright 2008-2018 Univa Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from pysphere import VIProperty
from tortuga.resourceAdapter.vmware_adapter import Vmware_adapter


template = 'centos-6.5-base'

adapter = Vmware_adapter()

conn = adapter._Vmware_adapter__connect_vsphere()

template_vm = conn.get_vm_by_name(template)

datacenter_mor = adapter.\
    _Vmware_adapter__get_datacenter_mor(
        conn, adapter._Vmware_adapter__vCenterDataCenter)

cluster_mor = None

datacenter_properties = VIProperty(conn, datacenter_mor)

hostfolder_mor = datacenter_properties.hostFolder._obj

destination_mor = adapter.\
    _Vmware_adapter__get_folder_mor(
        conn, datacenter_mor, adapter._Vmware_adapter__destinationPath) \
        if adapter._Vmware_adapter__destinationPath else \
        datacenter_properties.vmFolder._obj

# Get ComputeResource MORs
compute_resource_mors = conn.\
    _retrieve_properties_traversal(
        property_names=['name', 'host'],
        from_node=hostfolder_mor,
        obj_type='ComputeResource')

source_mor = cluster_mor if cluster_mor is not None else datacenter_mor

host_mor = adapter._Vmware_adapter__get_random_host_mor(conn, source_mor)

compute_resource_mor = adapter.\
    _Vmware_adapter__get_compute_resource_mor_by_host_mor(
        compute_resource_mors, host_mor)

compute_resource_properties = VIProperty(conn, compute_resource_mor)

resource_pool_mor = adapter.\
    _Vmware_adapter__get_resource_pool_mor_by_name(
        conn, adapter._Vmware_adapter__vCenterPool) \
        if adapter._Vmware_adapter__vCenterPool else \
        compute_resource_properties.resourcePool._obj

adapter._Vmware_adapter__cloneVm(template, 'delme4')
