"""Страницы главного окна."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from autoservice.application.context import AppContext


class Page(QWidget):
    def __init__(self, ctx: AppContext, win):
        super().__init__()
        self.ctx = ctx
        self.win = win
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(18, 14, 18, 14)

    def refresh(self) -> None:  # переопределяется
        pass

    def handle(self, action: str, **kwargs) -> None:  # переопределяется
        pass
