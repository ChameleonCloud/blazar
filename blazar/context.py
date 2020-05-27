# Copyright (c) 2013 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import threading

from oslo_context import context


class BlazarContext(context.RequestContext):

    _context_stack = threading.local()

    def __init__(self, service_catalog=None, **kwargs):
        # NOTE(neha-alhat): During serializing/deserializing context object
        # over the RPC layer, below extra parameters which are passed by
        # `oslo.messaging` are popped as these parameters are not required.
        kwargs.pop('client_timeout', None)
        kwargs.pop('user_identity', None)

        super(BlazarContext, self).__init__(**kwargs)

        self.service_catalog = service_catalog or []

        if self.is_admin and 'admin' not in self.roles:
            self.roles.append('admin')

    def __enter__(self):
        try:
            stack = self._context_stack.stack
        except AttributeError:
            stack = []
            self._context_stack.stack = stack
        stack.append(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        res = self._context_stack.stack.pop()
        assert res is self, "self should be the top element of the stack"

    @classmethod
    def current(cls):
        try:
            return cls._context_stack.stack[-1]
        except (AttributeError, IndexError):
            raise RuntimeError("Context isn't available here")

    def to_dict(self):
        result = super(BlazarContext, self).to_dict()
        result['service_catalog'] = self.service_catalog
        return result

    @classmethod
    def elevated(cls):
        try:
            ctx = cls.current()
        except RuntimeError:
            ctx = None
        return cls(ctx, is_admin=True)


def current():
    return BlazarContext.current()


def elevated():
    return BlazarContext.elevated()
