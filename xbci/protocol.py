# SPDX-License-Identifier: AGPL-3.0-only
class ProtocolError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
