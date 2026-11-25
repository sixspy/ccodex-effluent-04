"""effluent-04 统一业务冲突错误：响应体顶层带 code，便于验收区分不同服务的拒绝。"""
from __future__ import annotations


class ApiConflict(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class LockBusy(ApiConflict):
    def __init__(self, message: str = "批锁被其他会话持有", code: str = "lock_busy"):
        super().__init__(code, message, 409)


class MetricFrozenAfterSeal(ApiConflict):
    def __init__(self, message: str):
        super().__init__("metric_frozen_after_seal", message, 409)
