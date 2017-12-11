"""
Copyright (c) 2015 SONATA-NFV
ALL RIGHTS RESERVED.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Neither the name of the SONATA-NFV [, ANY ADDITIONAL AFFILIATION]
nor the names of its contributors may be used to endorse or promote
products derived from this software without specific prior written
permission.

This work has been performed in the framework of the SONATA project,
funded by the European Commission under Grant number 671517 through
the Horizon 2020 and 5G-PPP programmes. The authors would like to
acknowledge the contributions of their colleagues of the SONATA
partner consortium (www.sonata-nfv.eu).
"""

import logging
import yaml
import configparser
from sonsmbase.smbase import sonSMbase
from .ssh import Client

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("fsm-start-stop-configure")
LOG.setLevel(logging.DEBUG)
logging.getLogger("son-mano-base:messaging").setLevel(logging.INFO)


class CssFSM(sonSMbase):

    def __init__(self):

        """
        :param specific_manager_type: specifies the type of specific manager
        that could be either fsm or ssm.
        :param service_name: the name of the service that this specific manager
        belongs to.
        :param function_name: the name of the function that this specific
        manager belongs to, will be null in SSM case
        :param specific_manager_name: the actual name of specific manager
        (e.g., scaling, placement)
        :param id_number: the specific manager id number which is used to
        distinguish between multiple SSM/FSM that are created for the same
        objective (e.g., scaling with algorithm 1 and 2)
        :param version: version
        :param description: description
        """

        self.specific_manager_type = 'fsm'
        self.service_name = 'vcdn'
        self.function_name = 'vtu'
        self.specific_manager_name = 'css'
        self.id_number = '1'
        self.version = 'v0.1'
        self.description = "An FSM that subscribes to start, stop and configuration topic"

        super(self.__class__, self).__init__(specific_manager_type=self.specific_manager_type,
                                             service_name=self.service_name,
                                             function_name=self.function_name,
                                             specific_manager_name=self.specific_manager_name,
                                             id_number=self.id_number,
                                             version=self.version,
                                             description=self.description)

    def on_registration_ok(self):

        # The fsm registration was successful
        LOG.debug("Received registration ok event.")

        # send the status to the SMR
        status = 'Subscribed, waiting for alert message'
        message = {'name': self.specific_manager_id,
                   'status': status}
        self.manoconn.publish(topic='specific.manager.registry.ssm.status',
                              message=yaml.dump(message))

        # Subscribing to the topics that the fsm needs to listen on
        topic = "generic.fsm." + str(self.sfuuid)
        self.manoconn.subscribe(self.message_received, topic)
        LOG.info("Subscribed to " + topic + " topic.")

    def message_received(self, ch, method, props, payload):
        """
        This method handles received messages
        """

        # Decode the content of the message
        request = yaml.load(payload)

        # Don't trigger on non-request messages
        if "fsm_type" not in request.keys():
            LOG.info("Received a non-request message, ignoring...")
            return

        # Create the response
        response = None

        # the 'fsm_type' field in the content indicates for which type of
        # fsm this message is intended. In this case, this FSM functions as
        # start, stop and configure FSM
        if str(request["fsm_type"]) == "start":
            LOG.info("Start event received: " + str(request["content"]))
            response = self.start_event(request["content"])

        if str(request["fsm_type"]) == "stop":
            LOG.info("Stop event received: " + str(request["content"]))
            response = self.stop_event(request["content"])

        if str(request["fsm_type"]) == "configure":
            LOG.info("Config event received: " + str(request["content"]))
            response = self.configure_event(request["content"])

        if str(request["fsm_type"]) == "scale":
            LOG.info("Scale event received: " + str(request["content"]))
            response = self.scale_event(request["content"])

        # If a response message was generated, send it back to the FLM
        if response is not None:
            # Generated response for the FLM
            LOG.info("Response to request generated:" + str(response))
            topic = "generic.fsm." + str(self.sfuuid)
            corr_id = props.correlation_id
            self.manoconn.notify(topic,
                                 yaml.dump(response),
                                 correlation_id=corr_id)
            return

        # If response is None:
        LOG.info("Request received for other type of FSM, ignoring...")

    def start_event(self, content):
        """
        This method handles a start event.
        """
        LOG.info("Performing life cycle start event")
        LOG.info("content: " + str(content.keys()))

        # Extracting the ip of the management interface from the vnfr
        vm_image = "vtu-vnf"
        vnfr = content["vnfr"]

        if (content['vnfd']['name']) == vm_image:
            mgmt_ip = content['vnfr']['virtual_deployment_units'][0]['vnfc_instance'] [0]['connection_points'][0]['interface']['address']

        if not mgmt_ip:
            LOG.error("Couldn't obtain IP address from VNFR")
            return

        # Setting up ssh connection with the VNF
        ssh_client = Client(mgmt_ip, 'sonata', 'sonata', LOG, retries=10)

        # Extracting the ip of the service platform
#        sp_ip_coded = ssh_client.sendCommand('echo $SSH_CLIENT | cut -d" " -f 1')
#        LOG.info("extracted coded sp_ip: " + str(sp_ip_coded))
#        sp_ip = str(sp_ip_coded, 'utf-8')
        ips=[]
        sp_ip = ssh_client.sendCommand("echo $SSH_CLIENT | awk '{ print $1}'")
        LOG.info("extracted sp_ip: " + str(sp_ip))    
        if not self.validIP(sp_ip):
            LOG.info('Mon Config: Invalid SP IP')
            sp_ip = '10.30.0.112'
        ips.append(sp_ip)
        fl_exist = ssh_client.sendCommand('[ -f /etc/sonata_sp_address.conf ] && echo "True" || echo "False"')
        if fl_exist == "True":
            fl = ssh_client.sendCommand('cat /etc/sonata_sp_address.conf')
            conf_ip = fl.split('=')[1].rstrip()
            if conf_ip != sp_ip:
                ips.append(conf_ip)
        else:
            LOG.info('Mon Config: /etc/sonata_sp_address.conf NOT FOUND')

        # Configuring the monitoring probe
        LOG.info('Mon Config: Create new conf file')
        self.createConf(ips, 4, 'vtu-vnf')
        ssh_client.sendFile('node.conf')
        ssh_client.sendCommand('ls /tmp/')
        ssh_client.sendCommand('sudo mv /tmp/node.conf /opt/Monitoring/node.conf')
        ssh_client.sendCommand('sudo service mon-probe restart')
        ssh_client.close()
        LOG.info('Mon Config: Completed')

        # else:
        #     LOG.error("Couldn't obtain SP IP address. Monitoring configuration aborted")

        #Configuring vTU docker container
        LOG.info('vTU Start: Start the vTU docker container')
        ssh_client = Client(mgmt_ip,'sonata','sonata',LOG)
        command = 'sed -i "s/API_IP=.*/API_IP=%s/g" .env' %(mgmt_ip)
        ssh_client.sendCommand(command)
        ssh_client.sendCommand('sudo docker-compose up -d')
        ssh_client.close()
        LOG.info('vTU Service Config: Completed')
        # Create a response for the FLM
        response = {}
        response['status'] = 'COMPLETED'

        # TODO: complete the response

        return response

    def stop_event(self, content):
        """
        This method handles a stop event.
        """
        LOG.info("Performing life cycle stop event")
        LOG.info("content: " + str(content.keys()))
        # TODO: Add the stop logic. The content is a dictionary that contains
        # the required data

        vnfr = content['vnfr']

        # Create a response for the FLM
        response = {}
        response['status'] = 'COMPLETED'

        # TODO: complete the response

        return response

    def configure_event(self, content):
        """
        This method handles a configure event.
        """
        LOG.info("Performing life cycle configure event")
        LOG.info("content: " + str(content.keys()))
        # TODO: Add the configure logic. The content is a dictionary that
        # contains the required data

        nsr = content['nsr']
        vnfrs = content['vnfrs']

        # Create a response for the FLM
        response = {}
        response['status'] = 'COMPLETED'

        # TODO: complete the response

        return response

    def scale_event(self, content):
        """
        This method handles a scale event.
        """
        LOG.info("Performing life cycle scale event")
        LOG.info("content: " + str(content.keys()))
        # TODO: Add the configure logic. The content is a dictionary that
        # contains the required data

        # Create a response for the FLM
        response = {}
        response['status'] = 'COMPLETED'

        # TODO: complete the response

        return response
    
    def createConf(self, pw_ips, interval, name):
        pwurl=[]
        config = configparser.RawConfigParser()
        config.add_section('vm_node')
        config.add_section('Prometheus')
        config.set('vm_node', 'node_name', name)
        config.set('vm_node', 'post_freq', interval)
        for ip in pw_ips:
            pwurl.append("http://"+ip+":9091/metrics")
        config.set('Prometheus', 'server_url', json.dumps(pwurl))  
        with open('node.conf', 'w') as configfile:    # save
            config.write(configfile)
    
        f = open('node.conf', 'r')
        LOG.debug('Mon Config-> '+"\n"+f.read())
        f.close()

    def validIP(address):
        parts = str(address).split(".")
        if len(parts) != 4:
            return False
        for item in parts:
            if not 0 <= int(item) <= 255:
                return False
        return True


def main():
    CssFSM()

if __name__ == '__main__':
    main()
