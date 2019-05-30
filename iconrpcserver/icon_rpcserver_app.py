# Copyright 2018 ICON Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import os
import sys
from typing import List
from urllib.parse import urlparse

import gunicorn
import gunicorn.app.base
from breakfast.breakfast import Breakfast
from breakfast.dispatcher_factory import BreakfastDispatcher, BreakfastWebSocketDispatcher
from earlgrey import asyncio, aio_pika
from gunicorn.six import iteritems
from iconcommons.icon_config import IconConfig
from iconcommons.logger import Logger

from iconrpcserver.default_conf.icon_rpcserver_config import default_rpcserver_config
from iconrpcserver.default_conf.icon_rpcserver_constant import ConfigKey, NodeType
from iconrpcserver.dispatcher.default import NodeDispatcher, WSDispatcher
from iconrpcserver.dispatcher.v2 import Version2Dispatcher
from iconrpcserver.dispatcher.v3 import Version3Dispatcher
from iconrpcserver.dispatcher.v3d import Version3DebugDispatcher
from iconrpcserver.icon_rpcserver_cli import ICON_RPCSERVER_CLI, ExitCode
from iconrpcserver.server.peer_service_stub import PeerServiceStub
from iconrpcserver.server.rest_property import RestProperty
from iconrpcserver.server.rest_server import Avail, Disable, Status
from iconrpcserver.utils import camel_to_upper_snake
from iconrpcserver.utils.message_queue.stub_collection import StubCollection
from typing import Coroutine


class StandaloneApplication(gunicorn.app.base.BaseApplication):
    """Web server runner by gunicorn.

    """

    def init(self, parser, opts, args):
        pass

    def __init__(self, app, options=None):
        self.options = options or {}
        self.application = app
        super(StandaloneApplication, self).__init__()

    def load_config(self):
        config = dict([(key, value) for key, value in iteritems(self.options)
                       if key in self.cfg.settings and value is not None])
        for key, value in iteritems(config):
            self.cfg.set(key.lower(), value)

    def load(self):
        return self.application


def main():

    # Response server name as loopchain, not gunicorn.
    gunicorn.SERVER_SOFTWARE = 'loopchain'

    # Parse arguments.
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", type=str, dest=ConfigKey.PORT, default=None,
                        help="rest_proxy port")
    parser.add_argument("-c", type=str, dest=ConfigKey.CONFIG, default=None,
                        help="json configure file path")
    parser.add_argument("-at", type=str, dest=ConfigKey.AMQP_TARGET, default=None,
                        help="amqp target info [IP]:[PORT]")
    parser.add_argument("-ak", type=str, dest=ConfigKey.AMQP_KEY, default=None,
                        help="key sharing peer group using queue name. use it if one more peers connect one MQ")
    parser.add_argument("-ch", dest=ConfigKey.CHANNEL, default=None,
                        help="icon score channel")
    parser.add_argument("-tbears", dest=ConfigKey.TBEARS_MODE, action='store_true',
                        help="tbears mode")

    args = parser.parse_args()

    conf_path = args.config

    if conf_path is not None:
        if not IconConfig.valid_conf_path(conf_path):
            print(f'invalid config file : {conf_path}')
            sys.exit(ExitCode.COMMAND_IS_WRONG.value)
    if conf_path is None:
        conf_path = str()

    conf = IconConfig(conf_path, default_rpcserver_config)
    conf.load()
    conf.update_conf(dict(vars(args)))
    Logger.load_config(conf)

    _run_async(_check_rabbitmq(conf[ConfigKey.AMQP_TARGET]))
    _run_async(_run(conf))


def _run_async(async_func):
    loop = asyncio.new_event_loop()
    return loop.run_until_complete(async_func)


def run_in_foreground(conf: 'IconConfig'):
    _run_async(_check_rabbitmq(conf[ConfigKey.AMQP_TARGET]))
    _run_async(_run(conf))


async def _check_rabbitmq(amqp_target: str):
    connection = None
    try:
        amqp_user_name = os.getenv("AMQP_USERNAME", "guest")
        amqp_password = os.getenv("AMQP_PASSWORD", "guest")
        connection = await aio_pika.connect(host=amqp_target, login=amqp_user_name, password=amqp_password)
        connection.connect()
    except ConnectionRefusedError:
        Logger.error("rabbitmq-service disable", ICON_RPCSERVER_CLI)
        exit(0)
    finally:
        if connection:
            await connection.close()


def ready(conf):
    StubCollection().amqp_target = conf[ConfigKey.AMQP_TARGET]
    StubCollection().amqp_key = conf[ConfigKey.AMQP_KEY]
    StubCollection().conf = conf

    async def ready_tasks():
        Logger.debug('rest_server:initialize')

        if conf.get(ConfigKey.TBEARS_MODE, False):
            channel_name = conf.get(ConfigKey.CHANNEL, 'loopchain_default')
            await StubCollection().create_channel_stub(channel_name)
            await StubCollection().create_channel_tx_creator_stub(channel_name)
            await StubCollection().create_icon_score_stub(channel_name)

            RestProperty().node_type = NodeType.CommunityNode
            RestProperty().rs_target = None
        else:
            await StubCollection().create_peer_stub()
            channels_info = await StubCollection().peer_stub.async_task().get_channel_infos()
            channel_name = None
            for channel_name, channel_info in channels_info.items():
                await StubCollection().create_channel_stub(channel_name)
                await StubCollection().create_channel_tx_creator_stub(channel_name)
                await StubCollection().create_icon_score_stub(channel_name)
            results = await StubCollection().peer_stub.async_task().get_channel_info_detail(channel_name)
            RestProperty().node_type = NodeType(results[6])
            RestProperty().rs_target = results[3]
            relay_target = StubCollection().conf.get(ConfigKey.RELAY_TARGET, None)
            RestProperty().relay_target = urlparse(relay_target).netloc \
                if urlparse(relay_target).scheme else relay_target

        Logger.debug(f'rest_server:initialize complete. '
                     f'node_type({RestProperty().node_type}), rs_target({RestProperty().rs_target})')

    return ready_tasks


async def _run(conf: 'IconConfig'):
    redirect_protocol_env = os.getenv(camel_to_upper_snake(ConfigKey.REDIRECT_PROTOCOL))
    if redirect_protocol_env:
        conf.update_conf({ConfigKey.REDIRECT_PROTOCOL: redirect_protocol_env})

    Logger.print_config(conf, ICON_RPCSERVER_CLI)

    # Setup port and host values.

    # Connect gRPC stub.
    PeerServiceStub().conf = conf
    PeerServiceStub().rest_grpc_timeout = \
        conf[ConfigKey.GRPC_TIMEOUT] + conf[ConfigKey.REST_ADDITIONAL_TIMEOUT]
    PeerServiceStub().rest_score_query_timeout = \
        conf[ConfigKey.SCORE_QUERY_TIMEOUT] + conf[ConfigKey.REST_ADDITIONAL_TIMEOUT]
    PeerServiceStub().set_stub_port(int(conf[ConfigKey.PORT]) -
                                    int(conf[ConfigKey.PORT_DIFF_REST_SERVICE_CONTAINER]),
                                    conf[ConfigKey.IP_LOCAL])

    rest_task: Coroutine = ready(conf)
    dispatch_list: List[BreakfastDispatcher] = [
        BreakfastDispatcher(handler=NodeDispatcher.dispatch, url='/api/node/<channel_name>', methods=['POST']),
        BreakfastDispatcher(handler=NodeDispatcher.dispatch, url='/api/node/', methods=['POST']),
        BreakfastWebSocketDispatcher(handler=WSDispatcher.dispatch, url='/api/node/<channel_name>'),

        BreakfastDispatcher(handler=Version2Dispatcher.dispatch, url='/api/v2', methods=['POST']),
        BreakfastDispatcher(handler=Version3Dispatcher.dispatch, url='/api/v3/<channel_name>', methods=['POST']),
        BreakfastDispatcher(handler=Version3Dispatcher.dispatch, url='/api/v3/', methods=['POST']),

        BreakfastDispatcher(handler=Version3DebugDispatcher.dispatch, url='/api/debug/v3/<channel_name>', methods=['POST']),
        BreakfastDispatcher(handler=Version3DebugDispatcher.dispatch, url='/api/debug/v3/', methods=['POST']),

        BreakfastDispatcher(handler=Disable.as_view(), url='/api/v1', methods=['POST', 'GET']),
        BreakfastDispatcher(handler=Status.as_view(), url='/api/v1/status/peer', methods=["GET"]),
        BreakfastDispatcher(handler=Avail.as_view(), url='/api/v1/avail/peer', methods=["GET"]),

        BreakfastWebSocketDispatcher(handler=WSDispatcher.dispatch, url='/api/ws/<channel_name>')
    ]

    breakfast = Breakfast(ip="0.0.0.0",
                          port=conf[ConfigKey.PORT],
                          is_used_http=True,
                          ssl_type=conf[ConfigKey.REST_SSL_TYPE],
                          dispatch_list=dispatch_list,
                          rest_task=rest_task)
    breakfast.run()

    Logger.debug(f"Run gunicorn webserver for HA. Port = {conf[ConfigKey.PORT]}")
    Logger.error("Rest App Done!")


# Run as gunicorn web server.
if __name__ == "__main__":
    main()
