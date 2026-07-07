from __future__ import annotations

import sys
import json
import re
import threading
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from PySide6.QtCore import QPoint, QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QDoubleSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dos_app.app.exporters import companion_folder, export_results
from dos_app.app.history import load_recent_jobs, save_recent_job
from dos_app.app.local_mirror import export_local_website
from dos_app.app.models import ScrapedPage, ScrapeSettings
from dos_app.app.scraper import ScrapeCallbacks, ScrapeEngine, normalize_url, normalized_mode, site_root


def app_icon_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "app_logo.png"


class ScrapeWorker(QObject):
    log = Signal(str)
    result = Signal(object)
    progress = Signal(int, int)
    finished = Signal(list)

    def __init__(self, settings: ScrapeSettings) -> None:
        super().__init__()
        self.settings = settings
        self.stop_event = threading.Event()

    @Slot()
    def run(self) -> None:
        callbacks = ScrapeCallbacks(
            log=self.log.emit,
            result=self.result.emit,
            progress=self.progress.emit,
        )
        engine = ScrapeEngine(self.settings, callbacks, self.stop_event)
        results = engine.run()
        self.finished.emit(results)

    def stop(self) -> None:
        self.stop_event.set()


class LocalWebsiteExportWorker(QObject):
    log = Signal(str)
    progress = Signal(int, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, results: list[ScrapedPage], settings: ScrapeSettings) -> None:
        super().__init__()
        self.results = list(results)
        self.settings = settings
        self.stop_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            folder = export_local_website(
                self.results,
                self.settings.output_folder,
                self.settings.user_agent,
                self.settings.timeout_seconds,
                log=self.log.emit,
                progress=self.progress.emit,
                stop_event=self.stop_event,
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished.emit(folder)

    def stop(self) -> None:
        self.stop_event.set()


class SitemapSelectionDialog(QDialog):
    def __init__(self, urls: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose sitemap URLs")
        self.resize(760, 520)

        self.list_widget = QListWidget()
        for url in urls:
            item = QListWidgetItem(url)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.list_widget.addItem(item)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Select the sitemap URLs to scan."))
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def selected_urls(self) -> list[str]:
        urls: list[str] = []
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                urls.append(item.text())
        return urls


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DOS Scraper")
        icon_path = app_icon_path()
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1180, 760)
        self.setAcceptDrops(True)
        self.results: list[ScrapedPage] = []
        self.worker: ScrapeWorker | None = None
        self.thread: QThread | None = None
        self.export_worker: LocalWebsiteExportWorker | None = None
        self.export_thread: QThread | None = None
        self._closing = False
        self.started_at: float | None = None
        self.total_links_found = 0
        self.total_images_found = 0
        self.total_documents_found = 0
        self.total_videos_found = 0
        self.error_count = 0
        self.last_progress_total = 0
        self.status_labels: dict[str, QLabel] = {}
        self.stat_labels: dict[str, QLabel] = {}

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com")

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Single Page", "Whole Website", "Page and Beyond", "Sitemap", "Custom Rule Scan"])
        self.mode_combo.currentTextChanged.connect(self._scan_type_changed)

        self.template_combo = QComboBox()
        self.template_combo.addItems(
            [
                "General Website",
                "Game Manuals",
                "ROM Hacks",
                "Game Mods",
                "Documentation",
                "Wiki Pages",
                "Developer APIs",
                "Images",
                "Videos",
                "GitHub Repositories",
            ]
        )
        self.template_panel_combo = QComboBox()
        self.template_panel_combo.addItems([self.template_combo.itemText(index) for index in range(self.template_combo.count())])
        self.template_combo.currentTextChanged.connect(self._sync_template_panel)
        self.template_panel_combo.currentTextChanged.connect(self._sync_template_toolbar)
        self.template_create_button = QPushButton("Create")
        self.template_create_button.clicked.connect(self._create_template)
        self.template_duplicate_button = QPushButton("Duplicate")
        self.template_duplicate_button.clicked.connect(self._duplicate_template)
        self.template_import_button = QPushButton("Import")
        self.template_import_button.clicked.connect(self._import_template)
        self.template_export_button = QPushButton("Export")
        self.template_export_button.clicked.connect(self._export_template)

        self.recent_combo = QComboBox()
        self.recent_combo.addItem("Recent jobs")
        for job in load_recent_jobs():
            label = f"{job.get('mode', '')}: {job.get('url', '')}"
            self.recent_combo.addItem(label, job)
        self.recent_combo.currentIndexChanged.connect(self._load_recent_job)

        self.delay_input = QDoubleSpinBox()
        self.delay_input.setRange(0, 120)
        self.delay_input.setSingleStep(0.25)
        self.delay_input.setValue(1.0)
        self.delay_input.setSuffix(" sec")

        self.max_pages_input = QSpinBox()
        self.max_pages_input.setRange(1, 100000)
        self.max_pages_input.setValue(25)

        self.crawl_depth_input = QSpinBox()
        self.crawl_depth_input.setRange(0, 1000)
        self.crawl_depth_input.setValue(2)

        self.timeout_input = QDoubleSpinBox()
        self.timeout_input.setRange(1, 300)
        self.timeout_input.setValue(15)
        self.timeout_input.setSuffix(" sec")

        self.user_agent_input = QLineEdit("DOS-Scraper/0.1 (+desktop app)")
        self.same_domain_checkbox = QCheckBox("Stay on same domain")
        self.same_domain_checkbox.setChecked(True)
        self.path_checkbox = QCheckBox("Restrict to starting path")
        self.start_links_checkbox = QCheckBox("Only crawl links discovered from start page")
        self.skip_media_checkbox = QCheckBox("Skip direct media files")
        self.skip_media_checkbox.setChecked(True)
        self.robots_checkbox = QCheckBox("Respect robots.txt")
        self.robots_checkbox.setChecked(True)
        self.robots_check_button = QPushButton("Check robots.txt")
        self.robots_check_button.clicked.connect(self._check_robots_txt)
        self.playwright_checkbox = QCheckBox("Use Playwright rendering")

        self.rules_preview = QPlainTextEdit()
        self.rules_preview.setReadOnly(True)
        self.rules_preview.setMaximumHeight(105)

        default_output_folder = Path.cwd() / "exports"
        default_output_folder.mkdir(parents=True, exist_ok=True)
        self.output_folder_input = QLineEdit(str(default_output_folder))
        self.browse_button = QPushButton("Browse")
        self.browse_button.clicked.connect(self._choose_output_folder)

        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self._start_scraping)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_scraping)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(lambda: self._log("WARNING Pause/resume is not available for the current scraper worker. Use Stop to cancel."))
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self._open_settings_panel)
        self.help_button = QPushButton("Help")
        self.help_button.clicked.connect(self._show_help)

        self.export_combo = QComboBox()
        self.export_combo.addItems(["CSV", "JSON", "TXT", "HTML", "Local Website"])
        self.export_button = QPushButton("Export")
        self.export_button.clicked.connect(self._export_results)
        self.auto_export_checkbox = QCheckBox("Auto export when finished")
        self.naming_rules_input = QLineEdit("{domain} - {title}")
        self.naming_rules_input.setPlaceholderText("{domain} - {title}")

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(2000)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search URLs, titles, metadata, exported data")
        self.search_input.textChanged.connect(self._filter_results)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "URL",
                "Page Title",
                "HTTP Status",
                "Links Found",
                "Images Found",
                "Documents Found",
                "Response Time",
                "Last Modified",
                "Scraped Time",
                "Export Status",
            ]
        )
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)
        self.table.itemDoubleClicked.connect(lambda _item: self._open_selected_url())
        self.table.itemSelectionChanged.connect(self._update_inspector)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 10):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        self.inspector = QPlainTextEdit()
        self.inspector.setReadOnly(True)
        self.explorer_tree = QTreeWidget()
        self.explorer_tree.setHeaderLabels(["Website Explorer"])
        self.explorer_tree.itemSelectionChanged.connect(self._update_inspector_from_tree)
        self.crawl_tree = QTreeWidget()
        self.crawl_tree.setHeaderLabels(["Crawl Visualization"])

        self.sidebar_tabs = QToolBox()
        self.workspace_tabs = QTabWidget()
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(1000)
        self.status_timer.timeout.connect(self._update_runtime_status)

        self._build_layout()
        self._connect_rule_preview()
        self._install_shortcuts()
        self._scan_type_changed(self.mode_combo.currentText())
        self._update_rules_preview()
        self._update_stats()

    def _build_layout(self) -> None:
        toolbar = self._build_top_toolbar()
        sidebar = self._build_sidebar()
        center = self._build_center_workspace()
        inspector = self._build_inspector_panel()

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(sidebar)
        main_splitter.addWidget(center)
        main_splitter.addWidget(inspector)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setStretchFactor(2, 0)
        main_splitter.setSizes([300, 720, 340])

        vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        vertical_splitter.addWidget(main_splitter)
        vertical_splitter.addWidget(self._build_console_panel())
        vertical_splitter.setStretchFactor(0, 1)
        vertical_splitter.setStretchFactor(1, 0)
        vertical_splitter.setSizes([610, 170])

        root = QVBoxLayout()
        root.setContentsMargins(6, 6, 6, 4)
        root.setSpacing(6)
        root.addWidget(toolbar)
        root.addWidget(vertical_splitter, 1)
        root.addWidget(self.progress)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)
        self._build_status_bar()
        self._apply_dark_theme()

    def _build_top_toolbar(self) -> QWidget:
        toolbar = QFrame()
        toolbar.setObjectName("TopToolbar")
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(9)
        self.url_input.setMinimumWidth(520)
        self.url_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.mode_combo.setMinimumWidth(145)
        self.mode_combo.setMaximumWidth(175)
        self.template_combo.setMinimumWidth(150)
        self.template_combo.setMaximumWidth(195)
        self.recent_combo.setMinimumWidth(150)
        self.recent_combo.setMaximumWidth(220)
        layout.addWidget(QLabel("URL:"))
        layout.addWidget(self.url_input, 10)
        layout.addWidget(QLabel("Scan Type"))
        layout.addWidget(self.mode_combo, 0)
        layout.addWidget(QLabel("Template"))
        layout.addWidget(self.template_combo, 0)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.pause_button)
        layout.addWidget(QLabel("Recent"))
        layout.addWidget(self.recent_combo, 0)
        layout.addWidget(self.settings_button)
        layout.addWidget(self.help_button)
        return toolbar

    def _build_sidebar(self) -> QWidget:
        scan_setup = QWidget()
        scan_layout = QVBoxLayout(scan_setup)
        scan_layout.setContentsMargins(8, 8, 8, 8)
        scan_layout.setSpacing(6)
        scan_layout.addWidget(QLabel("Category Templates"))
        scan_layout.addWidget(self.template_panel_combo)
        template_actions = QHBoxLayout()
        template_actions.addWidget(self.template_create_button)
        template_actions.addWidget(self.template_duplicate_button)
        template_actions.addWidget(self.template_import_button)
        template_actions.addWidget(self.template_export_button)
        scan_layout.addLayout(template_actions)
        scan_layout.addWidget(QLabel("Mode Preview"))
        scan_layout.addWidget(self.rules_preview)
        self.sidebar_tabs.addItem(scan_setup, "Scan Setup")
        self.sidebar_tabs.addItem(
            self._form_page(
                [
                    ("Max Pages", self.max_pages_input),
                    ("Crawl Depth", self.crawl_depth_input),
                    ("Delay", self.delay_input),
                    ("Timeout", self.timeout_input),
                    ("User Agent", self.user_agent_input),
                ]
            ),
            "Crawl Options",
        )

        filters = QWidget()
        filter_layout = QVBoxLayout(filters)
        filter_layout.setContentsMargins(8, 8, 8, 8)
        filter_layout.setSpacing(4)
        for widget in (
            self.same_domain_checkbox,
            self.path_checkbox,
            self.start_links_checkbox,
            self.skip_media_checkbox,
            self.robots_checkbox,
            self.playwright_checkbox,
        ):
            filter_layout.addWidget(widget)
        filter_layout.addWidget(self.robots_check_button)
        filter_layout.addStretch(1)
        self.sidebar_tabs.addItem(filters, "Filters")

        output = QWidget()
        output_layout = QVBoxLayout(output)
        output_layout.setContentsMargins(8, 8, 8, 8)
        output_layout.setSpacing(6)
        output_layout.addWidget(QLabel("Export Format"))
        output_layout.addWidget(self.export_combo)
        output_layout.addWidget(QLabel("Output Folder"))
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_folder_input)
        output_row.addWidget(self.browse_button)
        output_layout.addLayout(output_row)
        output_layout.addWidget(self.auto_export_checkbox)
        output_layout.addWidget(QLabel("Naming Rules"))
        output_layout.addWidget(self.naming_rules_input)
        output_layout.addWidget(self.export_button)
        output_layout.addStretch(1)
        self.sidebar_tabs.addItem(output, "Output")

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(280)
        sidebar.setMaximumWidth(420)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("DOS Control Panel"))
        layout.addWidget(self.sidebar_tabs, 1)
        return sidebar

    def _form_page(self, rows: list[tuple[str, QWidget]]) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(6)
        for label, widget in rows:
            form.addRow(label, widget)
        return page

    def _build_center_workspace(self) -> QWidget:
        results_panel = QWidget()
        layout = QVBoxLayout(results_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._build_stats_strip())
        layout.addWidget(self.search_input)
        layout.addWidget(self.table, 1)

        self.workspace_tabs.addTab(results_panel, "Results")
        self.workspace_tabs.addTab(self.explorer_tree, "Explorer")
        self.workspace_tabs.addTab(self.crawl_tree, "Crawl")

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addWidget(self.workspace_tabs)
        return panel

    def _build_stats_strip(self) -> QWidget:
        strip = QFrame()
        strip.setObjectName("StatsStrip")
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for key, label in (
            ("pages", "Pages Scanned"),
            ("links", "Links Found"),
            ("images", "Images Found"),
            ("documents", "Documents Found"),
            ("videos", "Videos Found"),
            ("errors", "Errors"),
            ("queue", "Queue Size"),
            ("elapsed", "Elapsed Time"),
            ("speed", "Average Speed"),
        ):
            layout.addWidget(self._stat_card(key, label))
        return strip

    def _stat_card(self, key: str, label: str) -> QWidget:
        card = QFrame()
        card.setObjectName("StatCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(6, 4, 6, 4)
        value = QLabel("0")
        value.setObjectName("StatValue")
        caption = QLabel(label)
        caption.setObjectName("StatLabel")
        layout.addWidget(value)
        layout.addWidget(caption)
        self.stat_labels[key] = value
        return card

    def _build_inspector_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Inspector")
        panel.setMinimumWidth(280)
        panel.setMaximumWidth(520)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)
        title = QLabel("Inspector")
        title.setObjectName("PanelTitle")
        layout.addWidget(title)
        layout.addWidget(self.inspector, 1)
        return panel

    def _build_console_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Console")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Console")
        title.setObjectName("PanelTitle")
        layout.addWidget(title)
        layout.addWidget(self.log_view, 1)
        return panel

    def _build_status_bar(self) -> None:
        self.statusBar().clearMessage()
        for key, label in (
            ("status", "Status: Ready"),
            ("current", "Current URL: -"),
            ("pages", "Pages: 0"),
            ("queue", "Queue: 0"),
            ("errors", "Errors: 0"),
            ("elapsed", "Elapsed: 0s"),
            ("speed", "Speed: 0.00/s"),
            ("memory", "Memory: n/a"),
            ("cpu", "CPU: n/a"),
        ):
            widget = QLabel(label)
            self.status_labels[key] = widget
            self.statusBar().addPermanentWidget(widget)

    def _apply_dark_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #161616; color: #f2f2f2; font-size: 12px; }
            QFrame#TopToolbar, QFrame#Sidebar, QFrame#Inspector, QFrame#Console { background: #1e1e1e; border: 1px solid #3a3a3a; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QTableWidget, QTreeWidget {
                background: #101010; color: #f3f3f3; border: 1px solid #444; selection-background-color: #303030; selection-color: #ffffff;
            }
            QTableWidget::item:selected, QTreeWidget::item:selected { background: #303030; color: #ffffff; }
            QPushButton { background: #2a2a2a; border: 1px solid #5a5a5a; padding: 3px 8px; }
            QPushButton:hover { border-color: #0000aa; background: #303858; }
            QPushButton:disabled { color: #777; background: #202020; }
            QToolBox::tab { background: #252525; border: 1px solid #444; padding: 4px; }
            QToolBox::tab:selected { background: #303030; color: white; border-left: 3px solid #0000aa; }
            QHeaderView::section { background: #252525; color: #f2f2f2; border: 1px solid #444; padding: 3px; }
            QFrame#StatCard { background: #202020; border: 1px solid #404040; }
            QLabel#StatValue { color: #ffffff; font-weight: bold; font-size: 14px; }
            QLabel#StatLabel { color: #bbbbbb; font-size: 10px; }
            QLabel#PanelTitle { color: #ffffff; font-weight: bold; padding: 2px; }
            QProgressBar { border: 1px solid #444; text-align: center; background: #101010; }
            QProgressBar::chunk { background: #0000aa; }
            """
        )

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: self.url_input.setFocus())
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.search_input.setFocus())
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self._export_results)
        QShortcut(QKeySequence("F5"), self, activated=self._start_scraping)

    def _open_settings_panel(self) -> None:
        self.sidebar_tabs.setCurrentIndex(1)
        self.max_pages_input.setFocus()
        self._set_status_text("status", "Status: Settings")
        self._log("INFO Settings panel opened.")

    def _sync_template_panel(self, text: str) -> None:
        if self.template_panel_combo.currentText() != text:
            index = self.template_panel_combo.findText(text)
            if index >= 0:
                self.template_panel_combo.setCurrentIndex(index)

    def _sync_template_toolbar(self, text: str) -> None:
        if self.template_combo.currentText() != text:
            index = self.template_combo.findText(text)
            if index >= 0:
                self.template_combo.setCurrentIndex(index)

    def _add_template_name(self, name: str) -> None:
        if self.template_combo.findText(name) < 0:
            self.template_combo.addItem(name)
            self.template_panel_combo.addItem(name)
        index = self.template_combo.findText(name)
        if index >= 0:
            self.template_combo.setCurrentIndex(index)

    def _create_template(self) -> None:
        self._add_template_name(f"Custom Template {self.template_combo.count() + 1}")
        self._log(f"SUCCESS Created template: {self.template_combo.currentText()}")

    def _duplicate_template(self) -> None:
        self._add_template_name(f"{self.template_combo.currentText()} Copy")
        self._log(f"SUCCESS Duplicated template: {self.template_combo.currentText()}")

    def _template_payload(self) -> dict[str, object]:
        settings = self._settings()
        return {
            "name": self.template_combo.currentText(),
            "mode": settings.mode,
            "max_pages": settings.max_pages,
            "crawl_depth": settings.crawl_depth,
            "delay_seconds": settings.delay_seconds,
            "timeout_seconds": settings.timeout_seconds,
            "user_agent": settings.user_agent,
            "respect_robots": settings.respect_robots,
            "stay_on_same_domain": settings.stay_on_same_domain,
            "restrict_to_starting_path": settings.restrict_to_starting_path,
            "only_crawl_start_links": settings.only_crawl_start_links,
            "skip_direct_media_files": settings.skip_direct_media_files,
            "use_playwright": settings.use_playwright,
        }

    def _export_template(self) -> None:
        path_text, _ = QFileDialog.getSaveFileName(self, "Export template", f"{self.template_combo.currentText()}.json", "JSON (*.json)")
        if not path_text:
            return
        Path(path_text).write_text(json.dumps(self._template_payload(), indent=2), encoding="utf-8")
        self._log(f"SUCCESS Exported template: {path_text}")

    def _import_template(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(self, "Import template", "", "JSON (*.json)")
        if not path_text:
            return
        try:
            payload = json.loads(Path(path_text).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "Template import failed", str(exc))
            return
        name = str(payload.get("name") or Path(path_text).stem)
        self._add_template_name(name)
        if payload.get("mode"):
            index = self.mode_combo.findText(str(payload["mode"]))
            if index >= 0:
                self.mode_combo.setCurrentIndex(index)
        for widget_name, key in (
            ("max_pages_input", "max_pages"),
            ("crawl_depth_input", "crawl_depth"),
            ("delay_input", "delay_seconds"),
            ("timeout_input", "timeout_seconds"),
        ):
            if key in payload:
                getattr(self, widget_name).setValue(payload[key])
        if payload.get("user_agent"):
            self.user_agent_input.setText(str(payload["user_agent"]))
        for widget_name, key in (
            ("robots_checkbox", "respect_robots"),
            ("same_domain_checkbox", "stay_on_same_domain"),
            ("path_checkbox", "restrict_to_starting_path"),
            ("start_links_checkbox", "only_crawl_start_links"),
            ("skip_media_checkbox", "skip_direct_media_files"),
            ("playwright_checkbox", "use_playwright"),
        ):
            if key in payload:
                getattr(self, widget_name).setChecked(bool(payload[key]))
        self._log(f"SUCCESS Imported template: {name}")

    def _show_help(self) -> None:
        QMessageBox.information(
            self,
            "DOS Help",
            "DOS means Digital Offline Scraper.\n\n"
            "Enter a URL, choose a scan type and template, then start the scan. "
            "Use the Results, Explorer, Crawl, Inspector, and Console panels to review the job.",
        )

    def _set_status_text(self, key: str, value: str) -> None:
        label = self.status_labels.get(key)
        if label:
            label.setText(value)

    def _update_runtime_status(self) -> None:
        elapsed = self._elapsed_seconds()
        speed = len(self.results) / elapsed if elapsed else 0.0
        self._set_status_text("elapsed", f"Elapsed: {int(elapsed)}s")
        self._set_status_text("speed", f"Speed: {speed:.2f}/s")
        self._update_stats()

    def _elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self.started_at)

    def _update_stats(self) -> None:
        elapsed = self._elapsed_seconds()
        speed = len(self.results) / elapsed if elapsed else 0.0
        values = {
            "pages": str(len(self.results)),
            "links": str(self.total_links_found),
            "images": str(self.total_images_found),
            "documents": str(self.total_documents_found),
            "videos": str(self.total_videos_found),
            "errors": str(self.error_count),
            "queue": str(max(self.last_progress_total - len(self.results), 0)),
            "elapsed": f"{int(elapsed)}s",
            "speed": f"{speed:.2f}/s",
        }
        for key, value in values.items():
            label = self.stat_labels.get(key)
            if label:
                label.setText(value)
        self._set_status_text("pages", f"Pages: {len(self.results)}")
        self._set_status_text("queue", f"Queue: {values['queue']}")
        self._set_status_text("errors", f"Errors: {self.error_count}")

    def _filter_results(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            result_index = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(result_index, int) or result_index >= len(self.results):
                continue
            page = self.results[result_index]
            haystack = " ".join(
                [
                    page.url,
                    page.title,
                    page.description,
                    page.keywords,
                    page.canonical_url,
                    page.content_type,
                    page.error,
                    page.text[:5000],
                ]
            ).lower()
            self.table.setRowHidden(row, bool(needle and needle not in haystack))

    def _show_table_context_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        actions = [
            ("Open URL", self._open_selected_url),
            ("Copy URL", self._copy_selected_url),
            ("Copy Title", self._copy_selected_title),
            ("View Source", self._view_selected_source),
            ("Re-scrape", self._rescrape_selected),
            ("Export Selected", self._export_selected),
            ("Delete Entry", self._delete_selected_entries),
        ]
        for label, handler in actions:
            action = QAction(label, self)
            action.triggered.connect(handler)
            menu.addAction(action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _selected_result_indexes(self) -> list[int]:
        indexes: set[int] = set()
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item is not None:
                stored = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(stored, int):
                    indexes.add(stored)
        return sorted(indexes)

    def _selected_page(self) -> ScrapedPage | None:
        indexes = self._selected_result_indexes()
        if not indexes:
            return None
        row = indexes[0]
        if row < 0 or row >= len(self.results):
            return None
        return self.results[row]

    def _open_selected_url(self) -> None:
        page = self._selected_page()
        if page:
            QDesktopServices.openUrl(QUrl(page.url))

    def _copy_selected_url(self) -> None:
        page = self._selected_page()
        if page:
            QApplication.clipboard().setText(page.url)

    def _copy_selected_title(self) -> None:
        page = self._selected_page()
        if page:
            QApplication.clipboard().setText(page.title)

    def _view_selected_source(self) -> None:
        page = self._selected_page()
        if not page:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("View Source")
        dialog.resize(900, 650)
        editor = QPlainTextEdit(page.raw_html or page.text or "No source captured.")
        editor.setReadOnly(True)
        layout = QVBoxLayout(dialog)
        layout.addWidget(editor)
        dialog.exec()

    def _rescrape_selected(self) -> None:
        page = self._selected_page()
        if not page:
            return
        if self.thread and self.thread.isRunning():
            QMessageBox.information(self, "Scrape running", "Stop the current scrape before re-scraping a selected URL.")
            return
        self.url_input.setText(page.url)
        mode_index = self.mode_combo.findText("Single Page")
        if mode_index >= 0:
            self.mode_combo.setCurrentIndex(mode_index)
        self._start_scraping()

    def _export_selected(self) -> None:
        selected = [self.results[index] for index in self._selected_result_indexes() if index < len(self.results)]
        if not selected:
            return
        settings = self._settings()
        default_path = settings.output_folder / f"{self._export_site_identifier(selected)}-selected-results.json"
        path_text, _ = QFileDialog.getSaveFileName(self, "Export selected results", str(default_path), "JSON (*.json);;CSV (*.csv);;Text (*.txt);;HTML (*.html)")
        if not path_text:
            return
        export_results(selected, Path(path_text))
        self._log(f"SUCCESS Exported selected result(s): {path_text}")

    def _delete_selected_entries(self) -> None:
        result_indexes = sorted(self._selected_result_indexes(), reverse=True)
        for index in result_indexes:
            if 0 <= index < len(self.results):
                del self.results[index]
        self._rebuild_results_table()
        self._rebuild_navigation_trees()
        self._recalculate_totals()
        self._update_stats()
        self._update_inspector()

    def _rebuild_results_table(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for index, page in enumerate(self.results):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._fill_result_row(row, page, index)
        self.table.setSortingEnabled(True)

    def _update_inspector(self) -> None:
        page = self._selected_page()
        if not page:
            self.inspector.setPlainText("No result selected.")
            return
        lines = [
            f"URL: {page.url}",
            f"Page Title: {page.title}",
            f"Meta Description: {page.description}",
            f"Keywords: {page.keywords}",
            f"Canonical URL: {page.canonical_url}",
            f"Page Size: {len(page.raw_html.encode('utf-8')) if page.raw_html else len(page.text.encode('utf-8'))} bytes",
            f"Content Type: {page.content_type}",
            f"HTTP Status: {page.status_code}",
            f"Scraped Time: {page.scraped_at}",
            f"Error: {page.error}",
            "",
            "Extracted Links:",
            *[f"  {link}" for link in page.links[:200]],
            "",
            "Images:",
            *[f"  {image}" for image in page.images[:200]],
            "",
            "PDFs:",
            *[f"  {link}" for link in page.links if link.lower().split('?', 1)[0].endswith(".pdf")][:200],
            "",
            "Scripts:",
            "  Script inventory requires local website export or source inspection.",
            "",
            "Notes:",
            "  Add notes in future template/job metadata.",
        ]
        self.inspector.setPlainText("\n".join(lines))

    def _update_inspector_from_tree(self) -> None:
        items = self.explorer_tree.selectedItems()
        if items:
            self.inspector.setPlainText(items[0].data(0, Qt.ItemDataRole.UserRole) or items[0].text(0))

    def _rebuild_navigation_trees(self) -> None:
        self.explorer_tree.clear()
        self.crawl_tree.clear()
        explorer_roots: dict[str, QTreeWidgetItem] = {}
        crawl_roots: dict[str, QTreeWidgetItem] = {}
        for page in self.results:
            self._add_url_to_tree(self.explorer_tree, explorer_roots, page.url, page)
            self._add_url_to_tree(self.crawl_tree, crawl_roots, page.url, page)
        self.explorer_tree.expandToDepth(1)
        self.crawl_tree.expandToDepth(1)

    def _add_url_to_tree(
        self,
        tree: QTreeWidget,
        roots: dict[str, QTreeWidgetItem],
        url: str,
        page: ScrapedPage,
    ) -> None:
        parsed = urlparse(url)
        domain = parsed.netloc or "local"
        root = roots.get(domain)
        if root is None:
            root = QTreeWidgetItem([domain])
            root.setData(0, Qt.ItemDataRole.UserRole, domain)
            roots[domain] = root
            tree.addTopLevelItem(root)
        parent = root
        parts = [part for part in parsed.path.strip("/").split("/") if part] or ["index.html"]
        for part in parts:
            child = self._find_child(parent, part)
            if child is None:
                child = QTreeWidgetItem([part])
                parent.addChild(child)
            parent = child
        parent.setData(0, Qt.ItemDataRole.UserRole, self._inspector_text_for_page(page))

    def _find_child(self, parent: QTreeWidgetItem, text: str) -> QTreeWidgetItem | None:
        for index in range(parent.childCount()):
            child = parent.child(index)
            if child.text(0) == text:
                return child
        return None

    def _inspector_text_for_page(self, page: ScrapedPage) -> str:
        return "\n".join(
            [
                f"URL: {page.url}",
                f"Title: {page.title}",
                f"Status: {page.status_code}",
                f"Links: {page.link_count}",
                f"Images: {page.image_count}",
                f"Content Type: {page.content_type}",
                f"Scraped: {page.scraped_at}",
            ]
        )

    def _document_count(self, page: ScrapedPage) -> int:
        extensions = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".zip", ".rar", ".7z")
        return sum(1 for link in page.links if link.lower().split("?", 1)[0].endswith(extensions))

    def _video_count(self, page: ScrapedPage) -> int:
        extensions = (".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv", ".mpd", ".m3u8")
        values = page.links + page.images
        return sum(1 for link in values if link.lower().split("?", 1)[0].endswith(extensions))

    def _recalculate_totals(self) -> None:
        self.total_links_found = sum(page.link_count for page in self.results)
        self.total_images_found = sum(page.image_count for page in self.results)
        self.total_documents_found = sum(self._document_count(page) for page in self.results)
        self.total_videos_found = sum(self._video_count(page) for page in self.results)
        self.error_count = sum(1 for page in self.results if page.error)

    def _settings(self) -> ScrapeSettings:
        return ScrapeSettings(
            url=self.url_input.text().strip(),
            mode=normalized_mode(self.mode_combo.currentText()),
            delay_seconds=float(self.delay_input.value()),
            max_pages=int(self.max_pages_input.value()),
            crawl_depth=int(self.crawl_depth_input.value()),
            timeout_seconds=float(self.timeout_input.value()),
            user_agent=self.user_agent_input.text().strip() or "DOS-Scraper/0.1 (+desktop app)",
            respect_robots=self.robots_checkbox.isChecked(),
            stay_on_same_domain=self.same_domain_checkbox.isChecked(),
            restrict_to_starting_path=self.path_checkbox.isChecked(),
            only_crawl_start_links=self.start_links_checkbox.isChecked(),
            skip_direct_media_files=self.skip_media_checkbox.isChecked(),
            use_playwright=self.playwright_checkbox.isChecked(),
            output_folder=Path(self.output_folder_input.text()).expanduser(),
        )

    def _connect_rule_preview(self) -> None:
        self.url_input.textChanged.connect(self._update_rules_preview)
        self.mode_combo.currentTextChanged.connect(self._update_rules_preview)
        self.same_domain_checkbox.stateChanged.connect(self._update_rules_preview)
        self.path_checkbox.stateChanged.connect(self._update_rules_preview)
        self.start_links_checkbox.stateChanged.connect(self._update_rules_preview)
        self.skip_media_checkbox.stateChanged.connect(self._update_rules_preview)
        self.robots_checkbox.stateChanged.connect(self._update_rules_preview)
        self.max_pages_input.valueChanged.connect(self._update_rules_preview)
        self.crawl_depth_input.valueChanged.connect(self._update_rules_preview)
        self.delay_input.valueChanged.connect(self._update_rules_preview)
        self.timeout_input.valueChanged.connect(self._update_rules_preview)

    def _scan_type_changed(self, scan_type: str) -> None:
        mode = normalized_mode(scan_type)
        crawl_enabled = mode in {"Whole Website", "Page and Beyond"}
        self.crawl_depth_input.setEnabled(crawl_enabled)
        self.same_domain_checkbox.setEnabled(mode != "Single Page")
        self.path_checkbox.setEnabled(mode == "Page and Beyond")
        self.start_links_checkbox.setEnabled(mode == "Page and Beyond")

        if mode == "Single Page":
            self.crawl_depth_input.setValue(0)
            self.same_domain_checkbox.setChecked(True)
            self.path_checkbox.setChecked(False)
            self.start_links_checkbox.setChecked(False)
        elif mode == "Whole Website":
            if self.crawl_depth_input.value() == 0:
                self.crawl_depth_input.setValue(5)
            self.same_domain_checkbox.setChecked(True)
            self.path_checkbox.setChecked(False)
            self.start_links_checkbox.setChecked(False)
        elif mode == "Page and Beyond":
            if self.crawl_depth_input.value() == 0:
                self.crawl_depth_input.setValue(2)
            self.same_domain_checkbox.setChecked(True)
            self.start_links_checkbox.setChecked(True)

    def _update_rules_preview(self, *_args: object) -> None:
        url = self.url_input.text().strip()
        normalized = normalize_url(url) if url else ""
        mode = normalized_mode(self.mode_combo.currentText())
        lines = [f"Scan: {mode}"]
        if normalized:
            lines.append(f"Start: {normalized}")
            if self.same_domain_checkbox.isChecked():
                lines.append(f"Allowed domain: {site_root(normalized)}")
            if self.path_checkbox.isChecked():
                path = normalized
                if not path.endswith("/"):
                    path = f"{path}/"
                lines.append(f"Allowed path prefix: {path}")
        if mode == "Single Page":
            lines.append("Follows links: no")
        elif mode == "Sitemap":
            lines.append("Source: sitemap URLs selected before start")
        else:
            lines.append(f"Depth: {self.crawl_depth_input.value()}")
            lines.append(f"Max pages: {self.max_pages_input.value()}")
        lines.append(f"Direct media/file URLs: {'skipped' if self.skip_media_checkbox.isChecked() else 'scanned'}")
        lines.append(f"Robots.txt: {'respected' if self.robots_checkbox.isChecked() else 'not enforced'}")
        self.rules_preview.setPlainText("\n".join(lines))

    def _choose_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output_folder_input.text())
        if folder:
            self.output_folder_input.setText(folder)

    def _check_robots_txt(self) -> None:
        settings = self._settings()
        if not settings.url:
            QMessageBox.warning(self, "Missing URL", "Enter a URL before checking robots.txt.")
            return

        normalized = normalize_url(settings.url)
        robots_url = urljoin(site_root(normalized), "/robots.txt")
        self._log(f"Loading robots.txt: {robots_url}")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            response = requests.get(
                robots_url,
                headers={"User-Agent": settings.user_agent},
                timeout=settings.timeout_seconds,
            )
        except requests.RequestException as exc:
            self._log(f"robots.txt load failed: {exc}")
            QApplication.restoreOverrideCursor()
            return
        finally:
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()

        if response.status_code == 404:
            self._log("robots.txt not found. Standard crawler behavior treats this as no robots restrictions.")
            return

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            self._log(f"robots.txt load failed: HTTP {response.status_code} - {exc}")
            return

        lines = response.text.splitlines()
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(lines)
        allowed = parser.can_fetch(settings.user_agent, normalized)

        self._log(f"robots.txt status: HTTP {response.status_code}")
        self._log(
            f"Current URL for user-agent '{settings.user_agent}': "
            f"{'allowed' if allowed else 'blocked by robots.txt'}"
        )
        self._log("robots.txt rules found:")
        for line in self._summarize_robots_lines(lines):
            self._log(line)

    def _summarize_robots_lines(self, lines: list[str]) -> list[str]:
        interesting_prefixes = ("user-agent:", "allow:", "disallow:", "crawl-delay:", "sitemap:")
        summary: list[str] = []
        for raw_line in lines:
            clean = raw_line.split("#", 1)[0].strip()
            if not clean:
                continue
            if clean.lower().startswith(interesting_prefixes):
                if clean.lower().startswith("disallow:") and clean.partition(":")[2].strip() == "":
                    clean = f"{clean} (blank means allow all)"
                summary.append(f"  {clean}")
            if len(summary) >= 200:
                summary.append("  ...truncated after 200 robots.txt rule lines")
                break
        if not summary:
            summary.append("  No User-agent, Allow, Disallow, Crawl-delay, or Sitemap lines were found.")
        return summary

    def _start_scraping(self) -> None:
        settings = self._settings()
        if not settings.url:
            QMessageBox.warning(self, "Missing URL", "Enter a URL to scrape.")
            return
        if not self._ensure_output_folder(settings.output_folder):
            return
        if settings.mode == "Sitemap":
            selected_urls = self._choose_sitemap_urls(settings)
            if selected_urls is None:
                return
            settings.selected_sitemap_urls = selected_urls

        self.results.clear()
        self.table.setRowCount(0)
        self.explorer_tree.clear()
        self.crawl_tree.clear()
        self.inspector.setPlainText("No result selected.")
        self.log_view.clear()
        self.progress.setValue(0)
        self.started_at = time.monotonic()
        self.total_links_found = 0
        self.total_images_found = 0
        self.total_documents_found = 0
        self.total_videos_found = 0
        self.error_count = 0
        self.last_progress_total = settings.max_pages
        self._update_stats()
        self._log("Starting scrape job.")
        save_recent_job(settings)

        self.worker = ScrapeWorker(settings)
        self.thread = QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self._log)
        self.worker.result.connect(self._add_result)
        self.worker.progress.connect(self._set_progress)
        self.worker.finished.connect(self._scrape_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.pause_button.setEnabled(True)
        self.status_timer.start()
        self._set_status_text("status", "Status: Scraping")

    def _choose_sitemap_urls(self, settings: ScrapeSettings) -> list[str] | None:
        self.log_view.clear()
        self._log("Loading sitemap URLs for selection.")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            callbacks = ScrapeCallbacks(
                log=self._log,
                result=lambda _page: None,
                progress=lambda _done, _total: None,
            )
            engine = ScrapeEngine(settings, callbacks, threading.Event())
            urls = engine.collect_sitemap_urls()
        finally:
            QApplication.restoreOverrideCursor()

        if not urls:
            QMessageBox.information(self, "No sitemap URLs", "No URLs were found in the sitemap.")
            return None

        dialog = SitemapSelectionDialog(urls, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        selected = dialog.selected_urls()
        if not selected:
            QMessageBox.information(self, "No URLs selected", "Select at least one sitemap URL to scan.")
            return None
        return selected[: settings.max_pages]

    def _ensure_output_folder(self, folder: Path) -> bool:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Output folder unavailable", f"Could not create output folder:\n{folder}\n\n{exc}")
            return False
        return True

    def _stop_scraping(self) -> None:
        if self.worker:
            self.worker.stop()
            self._log("Stop requested.")
            self.stop_button.setEnabled(False)
            self.pause_button.setEnabled(False)
            self._set_status_text("status", "Status: Stopping")
        if self.export_worker:
            self.export_worker.stop()
            self._log("Export stop requested.")
            self.stop_button.setEnabled(False)
            self._set_status_text("status", "Status: Stopping export")

    @Slot(str)
    def _log(self, message: str) -> None:
        level = "INFO"
        upper = message.upper()
        if "ERROR" in upper or "FAILED" in upper or "CRITICAL" in upper:
            level = "ERROR"
        elif "WARNING" in upper or "SKIPPED" in upper or "BLOCKED" in upper:
            level = "WARNING"
        elif "SUCCESS" in upper or "FINISHED" in upper or "EXPORTED" in upper:
            level = "SUCCESS"
        colors = {"INFO": "#ffffff", "SUCCESS": "#4cd964", "WARNING": "#ffd60a", "ERROR": "#ff453a"}
        safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.log_view.append(f'<span style="color:{colors[level]};">[{level}] {safe}</span>')

    @Slot(object)
    def _add_result(self, page: ScrapedPage) -> None:
        self.results.append(page)
        self.total_links_found += page.link_count
        self.total_images_found += page.image_count
        self.total_documents_found += self._document_count(page)
        self.total_videos_found += self._video_count(page)
        if page.error:
            self.error_count += 1
        self._set_status_text("current", f"Current URL: {page.url[:90]}")
        row = self.table.rowCount()
        self.table.setSortingEnabled(False)
        self.table.insertRow(row)
        self._fill_result_row(row, page, len(self.results) - 1)
        self.table.setSortingEnabled(True)
        self._rebuild_navigation_trees()
        self._update_stats()
        self._filter_results(self.search_input.text())

    def _fill_result_row(self, row: int, page: ScrapedPage, result_index: int) -> None:
        values = [
            page.url,
            page.title,
            "" if page.status_code is None else str(page.status_code),
            str(page.link_count),
            str(page.image_count),
            str(self._document_count(page)),
            "n/a",
            "",
            page.scraped_at,
            "Error" if page.error else "Captured",
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
            item.setData(Qt.ItemDataRole.UserRole, result_index)
            self.table.setItem(row, column, item)

    @Slot(int, int)
    def _set_progress(self, done: int, total: int) -> None:
        self.last_progress_total = max(total, self.last_progress_total, done)
        self.progress.setValue(min(100, int((done / max(total, 1)) * 100)))
        self._update_stats()

    @Slot(list)
    def _scrape_finished(self, results: list[ScrapedPage]) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.progress.setValue(100)
        self.status_timer.stop()
        self._set_status_text("status", f"Status: Finished: {len(results)} page(s)")
        self._update_runtime_status()
        if self.auto_export_checkbox.isChecked() and results:
            self._export_results()

    @Slot()
    def _thread_finished(self) -> None:
        self.worker = None
        self.thread = None

    @Slot()
    def _export_thread_finished(self) -> None:
        self.export_worker = None
        self.export_thread = None

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override name.
        if self.thread and self.thread.isRunning():
            self._closing = True
            if self.worker:
                self.worker.stop()
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.statusBar().showMessage("Stopping scraper before exit...")
            self._log("Close requested. Waiting for scraper to stop.")
            if not self.thread.wait(5000):
                event.ignore()
                self.statusBar().showMessage("Still stopping. Try closing again in a moment.")
                return
        if self.export_thread and self.export_thread.isRunning():
            self._closing = True
            if self.export_worker:
                self.export_worker.stop()
            self.export_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.statusBar().showMessage("Stopping export before exit...")
            self._log("Close requested. Waiting for export to stop.")
            if not self.export_thread.wait(5000):
                event.ignore()
                self.statusBar().showMessage("Export is still stopping. Try closing again in a moment.")
                return
        event.accept()

    def dragEnterEvent(self, event) -> None:  # noqa: N802, ANN001 - Qt override.
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802, ANN001 - Qt override.
        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0].toString()
        else:
            url = event.mimeData().text().strip()
        if url:
            self.url_input.setText(url)
            self._update_rules_preview()

    def _export_results(self) -> None:
        if not self.results:
            QMessageBox.information(self, "No results", "There are no scrape results to export.")
            return
        settings = self._settings()
        if not self._ensure_output_folder(settings.output_folder):
            return
        export_type = self.export_combo.currentText()
        if export_type == "Local Website":
            self._start_local_website_export(settings)
            return

        extension = export_type.lower()
        default_path = settings.output_folder / f"{self._export_site_identifier(self.results)}-scrape-results.{extension}"
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "Export results",
            str(default_path),
            "CSV (*.csv);;JSON (*.json);;Text (*.txt);;HTML (*.html)",
        )
        if not path_text:
            return
        try:
            export_path = Path(path_text)
            export_results(self.results, export_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Exported to {export_path} and {companion_folder(export_path)}")

    def _start_local_website_export(self, settings: ScrapeSettings) -> None:
        if self.export_thread and self.export_thread.isRunning():
            QMessageBox.information(self, "Export running", "A local website export is already running.")
            return

        self.export_worker = LocalWebsiteExportWorker(self.results, settings)
        self.export_thread = QThread()
        self.export_worker.moveToThread(self.export_thread)
        self.export_thread.started.connect(self.export_worker.run)
        self.export_worker.log.connect(self._log)
        self.export_worker.progress.connect(self._set_progress)
        self.export_worker.finished.connect(self._local_website_export_finished)
        self.export_worker.failed.connect(self._local_website_export_failed)
        self.export_worker.finished.connect(self.export_thread.quit)
        self.export_worker.failed.connect(self.export_thread.quit)
        self.export_worker.finished.connect(self.export_worker.deleteLater)
        self.export_worker.failed.connect(self.export_worker.deleteLater)
        self.export_thread.finished.connect(self._export_thread_finished)
        self.export_thread.finished.connect(self.export_thread.deleteLater)
        self.export_thread.start()

        self.export_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.progress.setValue(0)
        self.statusBar().showMessage("Exporting local website...")
        self._log("Started local website export.")

    def _export_site_identifier(self, results: list[ScrapedPage]) -> str:
        if results:
            first = results[0]
            parsed = urlparse(first.url)
            domain = parsed.netloc or "scrape"
            title = first.title.strip() or parsed.path.strip("/") or "results"
            value = f"{domain} - {title}"
        else:
            value = "scrape-results"
        value = re.sub(r"[^A-Za-z0-9._ -]+", "", value)
        value = re.sub(r"\s+", " ", value).strip(" ._-")
        return (value or "scrape-results")[:120]

    @Slot(object)
    def _local_website_export_finished(self, website_folder: Path) -> None:
        self.export_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.progress.setValue(100)
        self.statusBar().showMessage(f"Local website exported to {website_folder}")
        QMessageBox.information(self, "Local website exported", f"Open this file:\n{website_folder / 'OPEN_THIS.html'}")

    @Slot(str)
    def _local_website_export_failed(self, message: str) -> None:
        self.export_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.statusBar().showMessage("Local website export failed")
        QMessageBox.critical(self, "Local website export failed", message)

    def _load_recent_job(self, index: int) -> None:
        job = self.recent_combo.itemData(index)
        if not isinstance(job, dict):
            return
        self.url_input.setText(str(job.get("url", "")))
        mode = normalized_mode(str(job.get("mode", "Single Page")))
        mode_index = self.mode_combo.findText(mode)
        if mode_index >= 0:
            self.mode_combo.setCurrentIndex(mode_index)
        self.delay_input.setValue(float(job.get("delay_seconds", 1.0)))
        self.max_pages_input.setValue(int(job.get("max_pages", 25)))
        self.crawl_depth_input.setValue(int(job.get("crawl_depth", 2)))
        self.timeout_input.setValue(float(job.get("timeout_seconds", 15.0)))
        self.user_agent_input.setText(str(job.get("user_agent", "DOS-Scraper/0.1 (+desktop app)")))
        self.same_domain_checkbox.setChecked(bool(job.get("stay_on_same_domain", True)))
        self.path_checkbox.setChecked(bool(job.get("restrict_to_starting_path", False)))
        self.start_links_checkbox.setChecked(bool(job.get("only_crawl_start_links", False)))
        self.skip_media_checkbox.setChecked(bool(job.get("skip_direct_media_files", True)))
        self.robots_checkbox.setChecked(bool(job.get("respect_robots", True)))
        self.playwright_checkbox.setChecked(bool(job.get("use_playwright", False)))
        self.output_folder_input.setText(str(job.get("output_folder", Path.cwd() / "exports")))
        self._update_rules_preview()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DOS Scraper")
    icon_path = app_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()
