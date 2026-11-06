"""Исключения бизнес-логики. Тексты показываются менеджеру как есть."""


class DomainError(Exception):
    """Нарушение бизнес-правила. Сообщение на русском, понятное пользователю."""


class ValidationError(DomainError):
    pass


class NotFound(DomainError):
    pass


class PermissionDenied(DomainError):
    pass


class InsufficientStock(DomainError):
    pass


class AlreadyPosted(DomainError):
    pass


class DuplicateDocument(DomainError):
    pass


class InvalidStatusTransition(DomainError):
    pass


class AuthError(DomainError):
    pass
