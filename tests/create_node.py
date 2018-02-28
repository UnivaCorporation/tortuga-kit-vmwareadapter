#!/usr/bin/env python

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

from tortuga.db.dbManager import DbManager
from tortuga.db.hardwareProfilesDbHandler import HardwareProfilesDbHandler
from tortuga.db.softwareProfilesDbHandler import SoftwareProfilesDbHandler

from tortuga.node import nodeApiFactory


dbSession = DbManager().openSession()


dbHardwareProfile = HardwareProfilesDbHandler().getHardwareProfile(
    dbSession, 'vmware3')

dbSoftwareProfile = SoftwareProfilesDbHandler().getSoftwareProfile(
    dbSession, 'BasicCompute')


addNodeRequest = {
    'name': 'compute-1.isurfer.ca',
    'nics': [
        {
            'device': 0,
            'ip': '192.168.1.129',
        },
    ]
}

node_api = nodeApiFactory.getNodeApi()

node = node_api.createNewNode(dbSession, addNodeRequest, dbHardwareProfile,
                               dbSoftwareProfile, validateIp=False,
                               bGenerateIp=False)

node.state = 'Installed'

dbSession.commit()
