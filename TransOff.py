import sys
import time
import threading
import logging
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QRadioButton, QButtonGroup,
    QShortcut, QPlainTextEdit, QSystemTrayIcon, QMenu, QAction
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt5.QtGui import QKeySequence, QFont, QIcon
from transformers import MarianMTModel, MarianTokenizer
import pyperclip
from pynput import keyboard
from pynput.keyboard import Key, Controller


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(filename)s:%(lineno)d %(funcName)s() - %(message)s',
    handlers=[
        logging.FileHandler('translator.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class SignalEmitter(QObject):
    activate = pyqtSignal(str)
    model_loaded = pyqtSignal()  # 新增信号
    model_load_error = pyqtSignal(str)  # 新增信号

# 模型名称映射
MODEL_PATH = {
    "en2zh": "./models/en2zh",
    "zh2en": "./models/zh2en"
}

class TranslationWorker(QThread):
    translated = pyqtSignal(str)

    def __init__(self, text, model, tokenizer):
        super().__init__()
        self.text = text
        self.model = model
        self.tokenizer = tokenizer

    def run(self):
        try:
            # 分句翻译（支持多行）
            sentences = [sent.strip() for sent in self.text.split('\n') if sent.strip()]
            translated = []
            for sent in sentences:
                tok = self.tokenizer([sent], return_tensors="pt")
                with self.model.device:
                    output = self.model.generate(**tok,
                            repetition_penalty=1.5, # 防止重复
                            no_repeat_ngram_size=2) # 防止 n-gram 重复
                result = self.tokenizer.decode(output[0], skip_special_tokens=True)
                translated.append(result)
            self.translated.emit('\n'.join(translated))
        except Exception as e:
            self.translated.emit(f"[Error] {str(e)}")


class TranslatorApp(QMainWindow):
    def __init__(self):
        start_time = time.time()
        logger.info("开始初始化TranslatorApp")
        super().__init__()
        self.current_mode = "en2zh"  # 默认英译中
        self.model = None
        self.tokenizer = None
        self.translator_thread = None
        self.start_hotkey_listener()
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        # 初始化 signal_emitter
        self.signal_emitter = SignalEmitter()
        
        logger.debug(f"基础初始化完成，耗时: {time.time() - start_time:.2f}秒")
        
        # 创建系统托盘图标
        tray_start = time.time()
        self.create_tray_icon()
        logger.debug(f"创建托盘图标完成，耗时: {time.time() - tray_start:.2f}秒")
        
        # 初始化UI
        ui_start = time.time()
        self.init_ui()
        logger.debug(f"初始化UI完成，耗时: {time.time() - ui_start:.2f}秒")
        
        # 异步加载模型
        model_start = time.time()
        self.load_model_async()
        logger.debug(f"启动模型加载线程完成，耗时: {time.time() - model_start:.2f}秒")
        
        logger.info(f"TranslatorApp初始化完成，总耗时: {time.time() - start_time:.2f}秒")

    def create_tray_icon(self):
        logger.debug("开始创建系统托盘图标")
        # 创建系统托盘图标
        self.tray_icon = QSystemTrayIcon(self)
        
        # 设置图标（可以使用默认图标或自定义图标）
        self.tray_icon.setIcon(QApplication.style().standardIcon(QApplication.style().SP_ComputerIcon))
        
        # 创建右键菜单
        tray_menu = QMenu()
        restore_action = QAction("显示窗口", self)
        restore_action.triggered.connect(self.show_window)
        tray_menu.addAction(restore_action)
        
        quit_action = QAction("退出程序", self)
        quit_action.triggered.connect(self.quit_application)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        
        # 连接托盘图标激活信号（例如单击显示窗口）
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        
        # 显示托盘图标
        self.tray_icon.show()
        logger.debug("系统托盘图标创建完成")

    def on_tray_icon_activated(self, reason):
        # 当用户点击托盘图标时显示窗口
        if reason == QSystemTrayIcon.Trigger:
            self.show_window()

    def show_window(self):
        # 显示并激活窗口
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_application(self):
        # 退出应用程序
        QApplication.quit()

    def init_ui(self):
        logger.debug("开始初始化UI")
        ui_start = time.time()
        self.setWindowTitle("TransOff")
        self.setGeometry(300, 300, 800, 400)

        container = QWidget()
        layout = QVBoxLayout()

        mode_layout = QHBoxLayout()
        self.en2zh_btn = QRadioButton("英译中")
        self.zh2en_btn = QRadioButton("中译英")
        self.translate_btn = QPushButton("翻译")
        self.clear_btn = QPushButton("清空")
        self.en2zh_btn.setChecked(True)
        self.mode_group = QButtonGroup()
        self.mode_group.addButton(self.en2zh_btn)
        self.mode_group.addButton(self.zh2en_btn)
        # 注意：translate_btn 和 clear_btn 不应该添加到 ButtonGroup 中
        mode_layout.addWidget(QLabel("翻译方向："))
        mode_layout.addWidget(self.en2zh_btn)
        mode_layout.addWidget(self.zh2en_btn)
        mode_layout.addWidget(self.translate_btn)
        mode_layout.addWidget(self.clear_btn)
        mode_layout.addStretch()
        layout.addLayout(mode_layout)

        self.en2zh_btn.toggled.connect(self.on_mode_change)
        self.translate_btn.clicked.connect(self.translate)
        self.clear_btn.clicked.connect(self.clear_all)

        # 输入输出区域
        io_layout = QHBoxLayout()
        self.input_text = QPlainTextEdit()
        self.output_text = QPlainTextEdit()

        self.input_text.setFont(QFont("Microsoft YaHei", 10))
        self.output_text.setFont(QFont("Microsoft YaHei", 10))

        io_layout.addWidget(self.input_text, 1)
        io_layout.addWidget(self.output_text, 1)

        layout.addLayout(io_layout)

        # 按钮行
        btn_layout = QHBoxLayout()

        self.copy_input_btn = QPushButton("复制原文")
        self.copy_output_btn = QPushButton("复制译文")
        btn_layout.addWidget(self.copy_input_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.copy_output_btn)

        layout.addLayout(btn_layout)

        # 状态栏
        self.statusBar().showMessage("正在加载模型...")

        container.setLayout(layout)
        self.setCentralWidget(container)

        # 事件连接
        self.copy_input_btn.clicked.connect(lambda: self.copy_text(self.input_text))
        self.copy_output_btn.clicked.connect(lambda: self.copy_text(self.output_text))

        # 快捷键
        QShortcut(QKeySequence("Ctrl+Return"), self, self.translate)
        QShortcut(QKeySequence("Alt+C"), self, self.copy_input_text)
        QShortcut(QKeySequence("Alt+V"), self, self.copy_output_text)
        
        logger.debug(f"UI初始化完成，耗时: {time.time() - ui_start:.2f}秒")

    def eventFilter(self, obj, event):
        # 确保输入法事件被正确处理
        if obj in [self.input_text, self.output_text]:
            if event.type() == event.InputMethod:
                return False  # 让控件自己处理输入法事件
        return super().eventFilter(obj, event)
        
    # 添加新的方法来处理 Alt+C 和 Alt+V 快捷键
    def copy_input_text(self):
        """使用 Alt+C 复制原文"""
        text = self.input_text.toPlainText()
        if text:
            pyperclip.copy(text)
            self.statusBar().showMessage("原文已复制", 1000)

    def copy_output_text(self):
        """使用 Alt+V 复制译文"""
        text = self.output_text.toPlainText()
        if text:
            pyperclip.copy(text)
            self.statusBar().showMessage("译文已复制", 1000)

    def paste_input(self):
        text = pyperclip.paste()
        if text:
            self.input_text.setPlainText(text)
            self.input_text.setFocus()

    def on_mode_change(self):
        self.current_mode = "en2zh" if self.en2zh_btn.isChecked() else "zh2en"
        self.statusBar().showMessage(f"切换为 {self.current_mode}", 1000)
        self.translate()  # 自动翻译

    def load_model_async(self):
        logger.info("开始异步加载模型")
        def load():
            load_start = time.time()
            logger.info("模型加载线程启动")
            try:
                model_path = MODEL_PATH[self.current_mode]
                logger.info(f"开始加载tokenizer，模型路径: {model_path}")
                tokenizer_start = time.time()
                self.tokenizer = MarianTokenizer.from_pretrained(model_path)
                logger.info(f"Tokenizer加载完成，耗时: {time.time() - tokenizer_start:.2f}秒")
                
                logger.info(f"开始加载model，模型路径: {model_path}")
                model_start = time.time()
                self.model = MarianMTModel.from_pretrained(model_path)
                logger.info(f"Model加载完成，耗时: {time.time() - model_start:.2f}秒")
                
                logger.info(f"模型加载完成，总耗时: {time.time() - load_start:.2f}秒")
                self.signal_emitter.model_loaded.emit()  # 通过signal_emitter发送信号
            except Exception as e:
                logger.error(f"模型加载失败: {str(e)}", exc_info=True)
                self.signal_emitter.model_load_error.emit(str(e))  # 通过signal_emitter发送错误信号

        thread = threading.Thread(target=load, daemon=True)
        thread.start()
        logger.info("模型加载线程已启动")
        
    def on_model_loaded(self):
        logger.info("收到模型加载完成信号")
        self.statusBar().showMessage("模型加载完成，可开始翻译", 3000)

    def on_model_load_error(self, error_msg):
        logger.error(f"收到模型加载错误信号: {error_msg}")
        self.statusBar().showMessage(f"加载失败: {error_msg}")
        
    def translate(self):
        if not self.model or not self.tokenizer:
            self.statusBar().showMessage("模型未加载，请稍等...", 2000)
            return

        text = self.input_text.toPlainText().strip()
        if not text:
            return

        # 切换模型（如果方向变了）
        target_model = MODEL_PATH[self.current_mode]
        if self.tokenizer.name_or_path != target_model:
            switch_start = time.time()
            logger.info("开始切换模型")
            self.tokenizer = MarianTokenizer.from_pretrained(target_model)
            self.model = MarianMTModel.from_pretrained(target_model)
            logger.info(f"模型切换完成，耗时: {time.time() - switch_start:.2f}秒")
            self.statusBar().showMessage("切换模型完成", 1000)

        self.translator_thread = TranslationWorker(text, self.model, self.tokenizer)
        self.translator_thread.translated.connect(self.on_translated)
        self.translator_thread.start()
        self.statusBar().showMessage("翻译中...")

    def on_translated(self, result):
        logger.debug(f"收到翻译结果: {result[:50]}...")  # 只记录前50个字符
        self.output_text.setPlainText(result)
        self.statusBar().showMessage("翻译完成", 1000)
        self.translator_thread = None

    def handle_activation(self, mode):
        time.sleep(0.2)
        old_clip = pyperclip.paste()
        logger.debug(f"剪贴板内容变化old_clip: {old_clip} ")
        kb = Controller()
        kb.press(Key.ctrl)
        kb.press('c')
        time.sleep(0.01)
        kb.release('c')
        kb.release(Key.ctrl)

        time.sleep(0.01)
        new_clip = pyperclip.paste()
        logger.debug(f"剪贴板内容变化new_clip: {new_clip}")
        self.show()
        self.raise_()
        self.activateWindow()
        text = new_clip if new_clip and new_clip != old_clip else ""
        # 根据模式设置翻译方向
        if mode == "en2zh":
            self.en2zh_btn.setChecked(True)
            self.current_mode = "en2zh"
        elif mode == "zh2en":
            self.zh2en_btn.setChecked(True)
            self.current_mode = "zh2en"
        
        if text.strip():
            self.input_text.setPlainText(text)
            self.translate()
        else:
            self.input_text.clear()
            self.output_text.clear()
    def start_hotkey_listener(self):
        """启动热键监听器"""
        try:
            self.hotkey_listener = keyboard.GlobalHotKeys({
                '<ctrl>+1': self._get_selected_text_and_emit_en2zh,
                '<ctrl>+2': self._get_selected_text_and_emit_zh2en
            })
            self.hotkey_listener.start()
            logger.info("全局热键监听器已启动")
        except Exception as e:
            logger.error(f"启动热键监听器失败: {e}", exc_info=True)

    def _get_selected_text_and_emit_en2zh(self):
        """获取选中文本并发送信号(英译中)"""
        self.signal_emitter.activate.emit("en2zh")

    def _get_selected_text_and_emit_zh2en(self):
        """获取选中文本并发送信号(中译英)"""
        self.signal_emitter.activate.emit("zh2en")
    # 添加清空输入输出内容的方法
    def clear_all(self):
        """清空输入和输出内容"""
        self.input_text.clear()
        self.output_text.clear()
        self.statusBar().showMessage("已清空内容", 1000)

def main():
    start_time = time.time()
    logger.info("应用程序启动")

    app = QApplication(sys.argv)
    logger.info("QApplication创建完成")
    
    app.setQuitOnLastWindowClosed(False)  # 防止窗口关闭时应用程序退出
    app.setStyle('Fusion')  # 或者使用系统默认样式
    logger.info("QApplication配置完成")
    
    window = TranslatorApp()
    logger.info("TranslatorApp实例创建完成")
    
    # 在这里正确连接信号
    window.signal_emitter.activate.connect(window.handle_activation)
    window.signal_emitter.model_loaded.connect(window.on_model_loaded)
    window.signal_emitter.model_load_error.connect(window.on_model_load_error)
    # 不要立即显示窗口，注释掉下面这行
    # window.show()
    logger.info(f"窗口显示完成，总启动时间: {time.time() - start_time:.2f}秒")
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()