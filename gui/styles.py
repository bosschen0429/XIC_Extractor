LIGHT_PALETTE: dict[str, str] = {
    "primary": "#505cc8",
    "primary_hover": "#414da9",
    "primary_soft": "#eceefb",
    "primary_text": "#ffffff",
    "brand": "#20252e",
    "success": "#1a7f37",
    "success_hover": "#15803d",
    "warning": "#9a6700",
    "error": "#cf222e",
    "border": "#dde1e7",
    "border_strong": "#cdd2dc",
    "bg_app": "#f3f4f6",
    "bg_card": "#ffffff",
    "bg_header": "#f7f8fa",
    "header_bg": "#f7f8fa",
    "header_border": "#dde1e7",
    "bg_row_alt": "#f7f8fa",
    "bg_subtle": "#fbfbfd",
    "selection": "#dfe3fa",
    "text": "#20252e",
    "text_muted": "#59626d",
    "icon": "#5b6470",
    "danger_soft": "#fff1ef",
    "danger_border": "#f3b7b0",
    "warn_banner": "#ff7043",
}

DARK_PALETTE: dict[str, str] = {
    "primary": "#d3ac68",
    "primary_hover": "#e3bf80",
    "primary_soft": "#3a342b",
    "primary_text": "#202328",
    "brand": "#25282d",
    "success": "#46b96e",
    "success_hover": "#54c97c",
    "warning": "#d9a441",
    "error": "#f1707a",
    "border": "#393d45",
    "border_strong": "#494e57",
    "bg_app": "#1c1e22",
    "bg_card": "#25282d",
    "bg_header": "#2b2e34",
    "header_bg": "#25282d",
    "header_border": "#393d45",
    "bg_row_alt": "#292c32",
    "bg_subtle": "#202328",
    "selection": "#514531",
    "text": "#e7e8eb",
    "text_muted": "#a6acb5",
    "icon": "#a6acb5",
    "danger_soft": "#3a2528",
    "danger_border": "#7a3f44",
    "warn_banner": "#d9663c",
}

APPLICATION_FONT_FAMILY = "Segoe UI Variable"
APPLICATION_FONT_POINT_SIZE = 10
UI_FONT_STACK = '"Segoe UI Variable", "Segoe UI", "Microsoft JhengHei UI", sans-serif'


def build_stylesheet(c: dict[str, str]) -> str:
    return f"""
QWidget {{
    color: {c["text"]};
    font-family: {UI_FONT_STACK};
    font-size: 10pt;
}}

/* Scope the app background to the real top-level containers only. A universal
   QWidget {{ background-color }} rule paints the app colour onto every nested
   widget (including a card's header label row and uncovered areas), making the
   section header read as a detached band of app background. Scoping it here lets
   a card's children fall back to transparent so they show the CARD colour, while
   the gaps between cards still show the app background. */
QMainWindow,
QDialog,
QWidget#app_root,
QWidget#detail_workspace,
QScrollArea#content_scroll,
QWidget#scroll_content {{
    background-color: {c["bg_app"]};
}}

/* Tab shell */
QTabWidget::pane {{
    border: none;
    background-color: {c["bg_app"]};
    top: -1px;
}}

QTabBar {{
    qproperty-drawBase: 0;
}}

QTabBar::tab {{
    background-color: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: {c["text_muted"]};
    font-size: 10.5pt;
    font-weight: 600;
    margin-right: 4px;
    padding: 9px 18px;
}}

QTabBar::tab:hover {{
    color: {c["text"]};
    background-color: {c["primary_soft"]};
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}

QTabBar::tab:selected {{
    color: {c["primary"]};
    border-bottom: 2px solid {c["primary"]};
}}

/* App shell: sidebar + branded header */
QWidget#app_root {{
    background-color: {c["bg_app"]};
}}

QFrame#sidebar {{
    background-color: {c["bg_card"]};
    border: none;
    border-right: 1px solid {c["border"]};
}}

QLabel#brand_title {{
    color: {c["text"]};
    font-size: 11pt;
    font-weight: 700;
    background: transparent;
}}

QLabel#brand_sub {{
    color: {c["text_muted"]};
    font-size: 8.5pt;
    font-weight: 600;
    background: transparent;
}}

QPushButton#nav_item {{
    background: transparent;
    border: none;
    border-radius: 8px;
    color: {c["text_muted"]};
    font-size: 10.5pt;
    font-weight: 600;
    padding: 11px 12px;
    text-align: left;
}}

QPushButton#nav_item:hover {{
    background-color: {c["bg_app"]};
    color: {c["text"]};
}}

QPushButton#nav_item:checked {{
    background-color: {c["primary_soft"]};
    color: {c["primary_hover"]};
    font-weight: 700;
}}

QFrame#app_header {{
    border: none;
    border-bottom: 1px solid {c["border"]};
    background-color: {c["bg_card"]};
}}

QLabel#header_title {{
    color: {c["text"]};
    font-size: 14pt;
    font-weight: 700;
    background: transparent;
}}

QLabel#header_subtitle {{
    color: {c["text_muted"]};
    font-size: 9.5pt;
    font-weight: 500;
    background: transparent;
}}

/* Menu bar */
QMenuBar {{
    background-color: {c["bg_card"]};
    border-bottom: 1px solid {c["border"]};
    padding: 2px 6px;
}}

QMenuBar::item {{
    background: transparent;
    padding: 6px 11px;
    border-radius: 6px;
}}

QMenuBar::item:selected {{
    background-color: {c["primary_soft"]};
    color: {c["primary_hover"]};
}}

QMenu {{
    background-color: {c["bg_card"]};
    border: 1px solid {c["border_strong"]};
    border-radius: 8px;
    padding: 4px;
}}

QMenu::item {{
    padding: 7px 24px 7px 16px;
    border-radius: 6px;
}}

QMenu::item:selected {{
    background-color: {c["primary_soft"]};
    color: {c["primary_hover"]};
}}

QMenu::separator {{
    height: 1px;
    background-color: {c["border"]};
    margin: 4px 8px;
}}

/* Section cards */
QFrame#section_card {{
    background-color: {c["bg_card"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
}}

QFrame#section_header {{
    background-color: transparent;
    border: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}

QLabel#section_title {{
    color: {c["text"]};
    font-size: 11.5pt;
    font-weight: 700;
}}

QLabel#section_subtitle {{
    color: {c["text_muted"]};
    font-size: 9pt;
}}

QLabel#field_label {{
    color: {c["text_muted"]};
    font-weight: 600;
}}

QLabel#hint {{
    color: {c["text_muted"]};
    font-size: 9pt;
}}

QLabel#field_error {{
    color: {c["error"]};
    font-size: 9pt;
}}

QLabel#results_error {{
    color: {c["error"]};
    font-weight: 600;
}}

QLabel#istd_warning {{
    background-color: {c["warn_banner"]};
    border-radius: 4px;
    color: #ffffff;
    font-weight: 600;
    padding: 8px 12px;
}}

/* Buttons */
QPushButton {{
    background-color: {c["bg_card"]};
    border: 1px solid {c["border_strong"]};
    border-radius: 5px;
    color: {c["text"]};
    font-weight: 600;
    padding: 7px 12px;
}}

QPushButton#details_toggle {{
    background: transparent;
    border: none;
    color: {c["text_muted"]};
    text-align: left;
    font-size: 9pt;
    padding: 5px 2px;
}}

QPushButton#details_toggle:hover {{
    color: {c["primary"]};
}}

QWidget#execution_bar {{
    background-color: {c["bg_card"]};
    border-top: 1px solid {c["border"]};
}}

QWidget#execution_bar QProgressBar {{
    min-height: 0;
    max-height: 4px;
    border: none;
    border-radius: 2px;
}}

QWidget#execution_bar QProgressBar::chunk {{
    border-radius: 2px;
}}

QPushButton:hover {{
    border-color: {c["primary"]};
    background-color: {c["primary_soft"]};
}}

QPushButton:pressed {{
    background-color: {c["selection"]};
}}

QPushButton:checked {{
    border-color: {c["primary"]};
    background-color: {c["primary_soft"]};
    color: {c["primary_hover"]};
}}

QPushButton:disabled {{
    color: {c["text_muted"]};
    background-color: {c["bg_header"]};
    border-color: {c["border"]};
}}

QPushButton#btn_run {{
    background-color: {c["primary"]};
    border-color: {c["primary"]};
    color: {c["primary_text"]};
    font-size: 10.5pt;
    font-weight: 700;
    padding: 9px 16px;
}}

QPushButton#btn_run:hover {{
    background-color: {c["primary_hover"]};
    border-color: {c["primary_hover"]};
}}

QPushButton#btn_run:disabled {{
    background-color: {c["bg_header"]};
    border-color: {c["border"]};
    color: {c["text_muted"]};
}}

QPushButton#btn_stop {{
    background-color: {c["error"]};
    border-color: {c["error"]};
    color: white;
    font-size: 10.5pt;
    font-weight: 700;
    padding: 9px 16px;
}}

QPushButton#btn_stop:hover {{
    background-color: #b91c2b;
    border-color: #b91c2b;
}}

QPushButton#btn_save,
QPushButton#btn_add,
QPushButton#apply_method {{
    background-color: {c["primary"]};
    border-color: {c["primary"]};
    color: {c["primary_text"]};
    font-weight: 700;
}}

QPushButton#btn_save:hover,
QPushButton#btn_add:hover,
QPushButton#apply_method:hover {{
    background-color: {c["primary_hover"]};
    border-color: {c["primary_hover"]};
}}

QPushButton#btn_delete {{
    background-color: {c["danger_soft"]};
    border-color: {c["danger_border"]};
    color: {c["error"]};
    font-weight: 700;
}}

QPushButton#btn_delete:hover {{
    background-color: {c["danger_border"]};
    border-color: {c["error"]};
}}

/* Collapsible section toggles (QToolButton) — flat, not a grey native button */
QToolButton {{
    background: transparent;
    border: none;
    color: {c["text_muted"]};
    font-weight: 600;
    padding: 6px 2px;
    text-align: left;
}}

QToolButton:hover {{
    color: {c["primary"]};
}}

QToolButton:checked {{
    color: {c["text"]};
}}

/* Tables */
QTableWidget {{
    background-color: {c["bg_card"]};
    alternate-background-color: {c["bg_row_alt"]};
    border: 1px solid {c["border"]};
    border-radius: 8px;
    gridline-color: {c["border"]};
    selection-background-color: {c["selection"]};
    selection-color: {c["text"]};
}}

QTableWidget::item:selected {{
    background-color: {c["selection"]};
    color: {c["text"]};
}}

QTableWidget::item:hover {{
    background-color: {c["primary_soft"]};
}}

QHeaderView::section {{
    background-color: {c["bg_header"]};
    border: none;
    border-bottom: 1px solid {c["border_strong"]};
    border-right: 1px solid {c["border"]};
    color: {c["text_muted"]};
    font-weight: 700;
    padding: 7px 9px;
}}

QTableCornerButton::section {{
    background-color: {c["bg_header"]};
    border: none;
}}

/* Progress */
QProgressBar {{
    background-color: {c["bg_header"]};
    border: 1px solid {c["border"]};
    border-radius: 7px;
    color: {c["text"]};
    font-weight: 600;
    min-height: 18px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {c["primary"]};
    border-radius: 6px;
}}

/* Status bar */
QStatusBar {{
    background-color: {c["bg_card"]};
    border-top: 1px solid {c["border"]};
    color: {c["text_muted"]};
}}

QStatusBar::item {{
    border: none;
}}

QLabel#status_state {{
    color: {c["text_muted"]};
    font-weight: 700;
    padding: 0 10px;
}}

QLabel#elapsed_label {{
    color: {c["text_muted"]};
    font-size: 9pt;
}}

QFrame#dist_track {{
    background-color: {c["bg_subtle"]};
    border-radius: 8px;
}}

/* Inputs */
QLineEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox {{
    background-color: {c["bg_card"]};
    border: 1px solid {c["border_strong"]};
    border-radius: 7px;
    color: {c["text"]};
    padding: 7px 9px;
    selection-background-color: {c["selection"]};
    selection-color: {c["text"]};
}}

QLineEdit:hover,
QSpinBox:hover,
QDoubleSpinBox:hover,
QComboBox:hover {{
    border-color: {c["primary"]};
}}

QLineEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QComboBox:focus {{
    border: 2px solid {c["primary"]};
    padding: 6px 8px;
}}

QLineEdit[invalid="true"] {{
    border-color: {c["error"]};
    background-color: {c["danger_soft"]};
}}

QLabel#ready_hint_ok {{
    color: {c["success"]};
    font-weight: 700;
}}

QLabel#ready_hint_block {{
    color: {c["warning"]};
    font-weight: 700;
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {c["bg_card"]};
    border: 1px solid {c["border_strong"]};
    border-radius: 6px;
    selection-background-color: {c["primary_soft"]};
    selection-color: {c["text"]};
    outline: none;
}}

/* Scrollbars */
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {c["border_strong"]};
    border-radius: 6px;
    min-height: 28px;
}}

QScrollBar::handle:vertical:hover {{
    background: #b3bac4;
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QToolTip {{
    background-color: {c["brand"]};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 6px 9px;
}}
"""


COLORS = LIGHT_PALETTE
STYLESHEET = build_stylesheet(LIGHT_PALETTE)

# The palette currently applied to the app. RichText/HTML content (which the
# stylesheet cannot reach) reads colours from here and is re-rendered on theme
# change via each view's refresh_theme().
ACTIVE: dict[str, str] = dict(LIGHT_PALETTE)


def set_active(palette: dict[str, str]) -> None:
    ACTIVE.clear()
    ACTIVE.update(palette)
