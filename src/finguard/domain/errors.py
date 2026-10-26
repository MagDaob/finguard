"""可由调用方分类处理的领域错误。"""


class DomainError(ValueError):
    code = "DOMAIN_ERROR"


class AccessDenied(DomainError):
    code = "ACCESS_DENIED"


class InvalidTransition(DomainError):
    code = "INVALID_TRANSITION"


class BindingConflict(DomainError):
    code = "BINDING_CONFLICT"


class UnknownTool(DomainError):
    code = "UNKNOWN_TOOL"


class DuplicateTool(DomainError):
    code = "DUPLICATE_TOOL"
