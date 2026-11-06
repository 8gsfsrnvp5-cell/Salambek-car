"""Оформление: сдержанная светлая тема, крупные элементы управления."""

ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
SIDEBAR = "#1f2937"
SIDEBAR_HOVER = "#374151"
SIDEBAR_ACTIVE = "#2563eb"
BG = "#f3f4f6"
CARD = "#ffffff"
BORDER = "#d1d5db"
TEXT = "#111827"
MUTED = "#6b7280"
DANGER = "#b91c1c"
WARN = "#b45309"
OK = "#15803d"

STYLE = f"""
QWidget {{ font-family: "Segoe UI", Arial, sans-serif; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QFrame#Sidebar {{ background: {SIDEBAR}; }}
QLabel#AppTitle {{ color: white; font-size: 14pt; font-weight: 600; padding: 14px 12px 6px 14px; }}
QLabel#AppSubtitle {{ color: #9ca3af; padding: 0 12px 12px 14px; }}
QPushButton#NavButton {{ color: #e5e7eb; background: transparent; border: none; text-align: left;
    padding: 9px 14px; font-size: 10.5pt; border-radius: 6px; margin: 1px 8px; }}
QPushButton#NavButton:hover {{ background: {SIDEBAR_HOVER}; }}
QPushButton#NavButton:checked {{ background: {SIDEBAR_ACTIVE}; color: white; font-weight: 600; }}
QLabel#PageTitle {{ font-size: 16pt; font-weight: 600; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Warning {{ color: {WARN}; }}
QLabel#Danger {{ color: {DANGER}; font-weight: 600; }}
QLabel#Ok {{ color: {OK}; }}
QFrame#Card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; }}
QLabel#CardValue {{ font-size: 15pt; font-weight: 600; }}
QLabel#CardTitle {{ color: {MUTED}; }}
QPushButton {{ background: white; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 14px; min-height: 22px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #9ca3af; background: #f3f4f6; }}
QPushButton#Primary {{ background: {ACCENT}; color: white; border: 1px solid {ACCENT}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#Primary:disabled {{ background: #93c5fd; border-color: #93c5fd; }}
QPushButton#Danger {{ color: {DANGER}; }}
QPushButton#Danger:disabled {{ color: #9ca3af; }}
QPushButton#Quick {{ padding: 10px 14px; font-size: 10.5pt; text-align: left; }}
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QDateEdit, QTextEdit, QPlainTextEdit {{
    background: white; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 6px; min-height: 22px; }}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QTableView {{ background: white; border: 1px solid {BORDER}; border-radius: 6px; gridline-color: #e5e7eb;
    selection-background-color: #dbeafe; selection-color: {TEXT}; alternate-background-color: #f9fafb; }}
QHeaderView::section {{ background: #f3f4f6; border: none; border-bottom: 1px solid {BORDER}; padding: 5px; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; background: white; top: -1px; }}
QTabBar::tab {{ padding: 7px 14px; background: #e5e7eb; border: 1px solid {BORDER}; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: white; font-weight: 600; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 6px; margin-top: 12px; padding: 10px 8px 8px 8px; background: white; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; font-weight: 600; }}
QStatusBar {{ background: white; border-top: 1px solid {BORDER}; }}
"""
