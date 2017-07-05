#!/usr/bin/env python
# -*- encoding: utf-8 -*-
#
# Copyright © 2013  Julien Danjou <julien@danjou.info>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import functools

from oslo_config import cfg
from oslo_log import log as logging
import oslo_messaging as messaging
from oslo_service import service

from blazar import context

LOG = logging.getLogger(__name__)


class RPCClient(object):
    def __init__(self, target):
        super(RPCClient, self).__init__()
        self._client = messaging.RPCClient(
            target=target,
            transport=messaging.get_rpc_transport(cfg.CONF),
        )

    def cast(self, name, **kwargs):
        ctx = context.current()
        self._client.cast(ctx.to_dict(), name, **kwargs)

    def call(self, name, **kwargs):
        ctx = context.current()
        return self._client.call(ctx.to_dict(), name, **kwargs)


class RPCServer(service.Service):
    def __init__(self, target):
        super(RPCServer, self).__init__()
        self._server = messaging.get_rpc_server(
            target=target,
            transport=messaging.get_rpc_transport(cfg.CONF),
            endpoints=[ContextEndpointHandler(self, target)],
            executor='eventlet',
        )

    def start(self):
        super(RPCServer, self).start()
        self.tg.add_thread(self._server.start)

    def stop(self):
        super(RPCServer, self).stop()
        self._server.stop()


class ContextEndpointHandler(object):
    def __init__(self, endpoint, target):
        self.__endpoint = endpoint
        self.target = target

    def __getattr__(self, name):
        try:
            method = getattr(self.__endpoint, name)

            def run_method(__ctx, **kwargs):
                with context.BlazarContext(**__ctx):
                    return method(**kwargs)

            return run_method
        except AttributeError:
            LOG.error("No %(method)s method found implemented in "
                      "%(class)s class",
                      {'method': name, 'class': self.__endpoint})


def with_empty_context(func):
    @functools.wraps(func)
    def decorator(*args, **kwargs):
        with context.BlazarContext():
            return func(*args, **kwargs)

    return decorator


def prepare_service(argv=[]):
    logging.setup(cfg.CONF, 'blazar')
