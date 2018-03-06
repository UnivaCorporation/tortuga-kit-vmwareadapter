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


class tortuga_kit_vmwareadapter::node::packages {
  require tortuga::packages

  include tortuga::config

  ensure_resource('package', 'perl', {'ensure' => 'installed'})

  file { "${tortuga::config::instroot}/scripts":
    ensure => directory,
  }

  file { "${tortuga::config::instroot}/scripts/vmware-tools-install":
    source  => 'puppet:///modules/tortuga_kit_vmware/vmware-tools-install',
    mode    => '0700',
    require => File["${tortuga::config::instroot}/scripts"],
  }
}

class tortuga_kit_vmwareadapter::node::service {
  exec { 'vmware-tools-install':
    command => "${tortuga::config::instroot}/scripts/vmware-tools-install",
    creates => '/etc/vmware-tools/configured',
  }
}

class tortuga_kit_vmwareadapter::node {
  # The dependency should ensure that tortuga_core is installed prior to
  # this recipe running.
  require tortuga_kit_base::core

  contain tortuga_kit_vmwareadapter::node::packages
  contain tortuga_kit_vmwareadapter::node::service
}
