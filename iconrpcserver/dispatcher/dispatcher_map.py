"""
Dispatch map.
 - Declare relations of URI and its handler.


Parameters
--------
handler: json - jsonrpcserver
    Activate when the request comes to URI.

uri: string[]
    URI list to awake handler function.

method: string[]
    Request method. Default value is 'GET' (>=3.7)

---------
"""

from collections import namedtuple

from sanic.views import HTTPMethodView

from iconrpcserver.dispatcher.default import NodeDispatcher, WSDispatcher
from iconrpcserver.dispatcher.v2 import Version2Dispatcher
from iconrpcserver.dispatcher.v3 import Version3Dispatcher
from iconrpcserver.dispatcher.v3d import Version3DebugDispatcher


# Basic class of Dispatcher

Dispatcher = namedtuple("Dispatcher", "handler uri method")
# Dispatcher = namedtuple("Dispatcher", "handler uri method", defaults=(None, None, "GET"))  # TODO: >=3.7

# Declare dispatcher in here
dispatchers = (
    Dispatcher(NodeDispatcher.dispatch, ["/api/node/", "/api/node/<channel_name>"], method="POST"),
    Dispatcher(Version2Dispatcher.dispatch, ["/api/v2"], method="POST"),
    Dispatcher(Version3Dispatcher.dispatch, ["/api/v3/", "/api/v3/<channel_name>"], method="POST"),
    Dispatcher(Version3DebugDispatcher.dispatch, ["/api/debug/v3/", "/api/debug/v3/<channel_name>"], method="POST"),

    # Dispatcher(Disable.as_view(), ["/api/v1"], ["POST", "GET"]),
    # Dispatcher(Status.as_view(), ["api/v1/status/peer"], "GET"),
    # Dispatcher(Avail.as_view(), ["api/v1/avail/peer"], "GET"),

    Dispatcher(NodeDispatcher.websocket_dispatch, ["/api/node/<channel_name>"], method="GET"),
    Dispatcher(WSDispatcher.dispatch, ["/api/ws/<channel_name>"], method="GET"),
)

# TODO: Remove dependencies from Sanic.
# class Status(HTTPMethodView):
#     async def get(self, request):
#         args = request.raw_args
#         channel_name = ServerComponents.conf[ConfigKey.CHANNEL] if args.get('channel') is None else args.get('channel')
#         return response.json(PeerServiceStub().get_status(channel_name))
#
#
# class Avail(HTTPMethodView):
#     async def get(self, request):
#         args = request.raw_args
#         channel_name = ServerComponents.conf[ConfigKey.CHANNEL] if args.get('channel') is None else args.get('channel')
#         status = HTTPStatus.OK
#         result = PeerServiceStub().get_status(channel_name)
#
#         if result['status'] != "Service is online: 0":
#             status = HTTPStatus.SERVICE_UNAVAILABLE
#
#         return response.json(result, status=status)
#
#
# class Disable(HTTPMethodView):
#     async def get(self, request):
#         return response.text("This api version not support any more!")
#
#     async def post(self, request):
#         return response.text("This api version not support any more!")
