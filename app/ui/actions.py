"""UI 事件绑定、背景任务调度与草稿生命周期。"""

import json
import logging
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QListWidgetItem, QMessageBox, QInputDialog, QFileDialog
from app.config import ROOT
from app.api import create_engine
from app.adapters.registry import allow_restore
from app.ui.candidates import candidate_title
from app.ui.theme import colors
from app.ui.workers import Task

log = logging.getLogger("assistant")


class WindowActions:
    def run_task(self, fn, done):
        if self.busy:
            return
        self.busy = True
        epoch = self.epoch
        self.update_actions()
        focus_manager = getattr(self, "focus_manager", None)
        if focus_manager is not None:
            focus_manager.save_foreground()
        task = Task(fn)
        self.task = task

        def finished(result, error):
            self.busy = False
            self.update_actions()
            if focus_manager is not None:
                focus_manager.restore_foreground()
            if epoch != self.epoch:
                return
            if error:
                self.notice.setText(error)
                if self.listen.isChecked():
                    self.listen.setChecked(False)
                return
            done(result)

        task.signals.done.connect(finished)
        self.pool.start(task)

    def _add_contact_items(self, contacts, caption=""):
        """把一组联系人加进列表；caption 非空时先插一行不可选的分组标题。"""
        if not contacts:
            return
        if caption:
            header = QListWidgetItem(caption)
            header.setFlags(Qt.NoItemFlags)
            header.setData(Qt.UserRole, None)
            # 不可选的分组标题要看起来就不像联系人，否则会被当成一个能点的会话。
            header.setForeground(QColor(colors()["muted"]))
            self.contacts.addItem(header)
        for contact in contacts:
            item = QListWidgetItem(
                f"{contact['name']}\n{contact['relationship'] or '关系未设置'}"
            )
            item.setData(Qt.UserRole, contact["id"])
            self.contacts.addItem(item)
            if contact["id"] == self.contact_id:
                self.contacts.setCurrentItem(item)

    def refresh_contacts(self):
        """只列当前平台的联系人；微信 / QQ / 钉钉各看各的，绝不混在一起。

        手工导入的会话不属于任何平台，单独分一组放在末尾并注明来源，
        这样既不会被误当成某个平台的真实联系人，也不会随平台切换消失。
        """
        platform = getattr(self, "current_platform", None) or self.adapter.get_platform_name()
        self.contacts.blockSignals(True)
        self.contacts.clear()
        self._add_contact_items(self.db.contacts(platform))
        self._add_contact_items(self.db.contacts("手工"), caption="本机手工会话")
        self.contacts.blockSignals(False)
        self.filter_contacts(self.search.text())

    def filter_contacts(self, text):
        keyword = text.lower()
        for i in range(self.contacts.count()):
            item = self.contacts.item(i)
            # 分组标题不参与搜索，但它下面全被过滤掉时也要跟着隐藏。
            if item.data(Qt.UserRole) is None:
                item.setHidden(not self._group_has_match(i, keyword))
                continue
            item.setHidden(keyword not in item.text().lower())

    def _group_has_match(self, header_row, keyword):
        for i in range(header_row + 1, self.contacts.count()):
            item = self.contacts.item(i)
            if item.data(Qt.UserRole) is None:
                break
            if keyword in item.text().lower():
                return True
        return False

    def select_contact(self, item, previous=None):
        if not item or self.busy or item.data(Qt.UserRole) is None:
            return
        self.contact_id = item.data(Qt.UserRole)
        self.snapshot = None
        self.batch = None
        self.incoming.clear()
        self.clear_candidates()
        self._sync_contact_hint()
        self.refresh_details()
        self.update_actions()

    def _sync_contact_hint(self):
        """把左栏选中的联系人告诉适配器，作为读不出会话名时的兜底。

        QQ NT 的窗口标题恒为"QQ"，无障碍树里也没有会话标题控件，不给这个
        兜底就连一次读取都完成不了。用了它的快照会标成未核实，发送前会提示。
        """
        adapter = getattr(self, "adapter", None)
        setter = getattr(adapter, "set_contact_hint", None)
        if setter is None or not self.contact_id:
            return
        try:
            setter(self.db.contact(self.contact_id)["name"])
        except Exception:
            setter("")

    def add_contact(self):
        if self.busy:
            return
        name, ok = QInputDialog.getText(self, "手工会话", "联系人名称（独立于真实微信）：")
        if ok and name.strip():
            self.contact_id = self.db.ensure_contact(name, "手工")
            self.snapshot = None
            self.batch = None
            self.clear_candidates()
            self.incoming.clear()
            self.refresh_contacts()
            self.refresh_details()

    def refresh_details(self):
        if not self.contact_id:
            return
        c = self.db.contact(self.contact_id)
        self.contact_title.setText(
            f"{c['name']}  ·  {c['relationship'] or '关系未设置'}  ·  {c['platform']}"
        )
        messages = self.db.recent_messages(self.contact_id, 100)
        self.chat.render_messages(messages)
        self.contact_page.load(self.contact_id)
        self.memory_page.load(self.contact_id)
        self.update_actions()

    def read_chat(self, monitored=False):
        if self.busy or self.gate.stopped.is_set():
            return
        platform = getattr(self, "current_platform", None) or self.adapter.get_platform_name()
        # 适配器可能刚因为切平台而重建，每次读取前都同步一次兜底会话名。
        self._sync_contact_hint()
        self.notice.setText(f"正在读取{platform}控件与最近消息…")

        def read():
            # 只有用户亲手点"读取"时才允许把托盘/最小化的窗口还原出来；
            # 监听轮询里这么做的话，客户端每 5 秒就会自己弹一次。
            if monitored:
                snapshot = self.adapter.inspect()
            else:
                with allow_restore(self.adapter):
                    snapshot = self.adapter.inspect()
            cid, count = self.engine.conversation.ingest(snapshot)
            return snapshot, cid, count

        def loaded(result):
            snapshot, cid, count = result
            # readable 为 False 表示只走到了某一阶段，绝不能当成读取成功。
            readable = bool(getattr(snapshot, "readable", True))
            key = (snapshot.window_handle, snapshot.contact_name)
            previous = (
                self.baselines.get(key) if getattr(self, "last_chat_key", None) == key else None
            )
            previous_ids = {m.source_id for m in previous.messages} if previous else set()
            current_ids = {m.source_id for m in snapshot.messages}
            fresh = bool(
                readable
                and previous
                and previous.messages
                and previous.messages[-1].source_id in current_ids
                and snapshot.messages
                and snapshot.messages[-1].source_id not in previous_ids
                and snapshot.messages[-1].sender not in {"我", "系统"}
                and snapshot.messages[-1].message_type == "text"
            )
            unchanged = bool(
                previous
                and previous.fingerprint == snapshot.fingerprint
                and self.snapshot == snapshot
                and self.contact_id == cid
            )
            self.baselines[key] = snapshot
            self.last_chat_key = key
            self.contact_id = cid
            self.snapshot = snapshot
            if not unchanged or not monitored:
                self.batch = None
                self.clear_candidates()
                self.incoming.blockSignals(True)
                # 只有可信读取才回填待回复内容，避免把待核验/界面文字送进模型。
                self.incoming.setPlainText(
                    snapshot.messages[-1].content if readable else ""
                )
                self.incoming.blockSignals(False)
                self._sync_pending_title()
                self.refresh_contacts()
                self.refresh_details()
            self.platform_connected = readable
            stage = str(getattr(snapshot, "detail", "") or "")
            self.platform_status.setText(
                f"{platform}：{'已读取会话' if readable else '窗口已连接，会话未读取'}  ·  "
                f"自动回复：{'开启' if self.gate.auto_enabled else '关闭'}"
            )
            if readable:
                unverified = (
                    ""
                    if getattr(snapshot, "contact_verified", True)
                    else f"　⚠ {platform}窗口里读不出会话名，已按左栏选中的"
                    f"「{snapshot.contact_name}」记账，请自行确认客户端停在该聊天上。"
                )
                self.notice.setText(
                    f"读取完成：可见消息 {len(snapshot.messages)} 条，"
                    f"本次新增保存 {count} 条。{unverified}"
                )
            else:
                self.notice.setText(
                    f"未完成读取（{snapshot.status}）：{stage}"
                    "。系统不会把联系人列表或导航栏文字当作聊天消息，"
                    "生成与发送已保持禁用。"
                )
                log.info(
                    "读取未完成 platform=%s status=%s messages=%d",
                    platform,
                    snapshot.status,
                    len(snapshot.messages),
                )
            self.update_actions()
            if monitored and fresh and self.db.auto_reply_enabled(cid):
                self.auto_fresh = True
                self.generate()

        self.run_task(read, loaded)

    def input_changed(self):
        self.batch = None
        self.snapshot = None
        self.auto_fresh = False
        self.clear_candidates()
        self.update_actions()

    def clear_candidates(self):
        self.candidates.clear()
        self.editor.clear()
        self.risk.setText("等待上下文")
        self.suggestions.set_expanded(False)
        self._sync_pending_title()

    def _sync_pending_title(self):
        """折叠面板的标题要能看出这一轮在回复哪条消息，收起来时也一样。"""
        incoming = " ".join(self.incoming.toPlainText().split())
        if not incoming:
            self.suggestions.set_title("AI 回复建议")
            return
        preview = incoming if len(incoming) <= 24 else incoming[:24] + "…"
        self.suggestions.set_title(f"AI 回复建议 · 待回复：{preview}")

    def generate(self):
        if not self.contact_id or self.busy:
            return
        incoming = self.incoming.toPlainText().strip()
        if not incoming:
            return
        if self.snapshot is not None and not getattr(self.snapshot, "readable", True):
            self.notice.setText(
                "当前会话读取结果不可信，已阻止生成回复。"
                f"失败阶段：{getattr(self.snapshot, 'detail', '') or self.snapshot.status}"
            )
            return
        self.batch = None
        self.clear_candidates()
        self.notice.setText("正在结合上下文、记忆与表达风格生成三个候选…")
        snapshot, cid, fresh = self.snapshot, self.contact_id, self.auto_fresh
        self.auto_fresh = False

        def ready(batch):
            self.batch = batch
            if not batch.should_reply:
                self.notice.setText(batch.reason)
                self.update_actions()
                return
            self.model_status.setText("DeepSeek：已连接")
            self.candidates.blockSignals(True)
            for candidate in batch.candidates:
                preview = candidate["text"].replace("\n", " ")
                item = QListWidgetItem(f"{candidate_title(candidate)}\n{preview}")
                item.setData(Qt.UserRole, candidate)
                item.setToolTip(candidate["text"] + "\n\n" + candidate["note"])
                item.setSizeHint(self.candidate_size_hint())
                self.candidates.addItem(item)
            self.candidates.blockSignals(False)
            self.candidates.setCurrentRow(0)
            # 生成完就自动展开：候选藏在折叠面板里不展开等于没生成。
            self.suggestions.set_title(f"AI 回复建议（{len(batch.candidates)} 条）")
            self.suggestions.set_expanded(True)
            self.notice.setText("三个候选已生成。选择后可直接修改，发送前请核对。")
            self.update_actions()
            if self.mode.currentText() == "辅助模式" and snapshot:
                self.deliver("fill")
            elif self.gate.auto_enabled and snapshot and fresh:
                self.deliver("send", automatic=True, fresh=True)

        self.run_task(lambda: self.engine.generate(cid, incoming, snapshot), ready)

    def choose(self, row):
        if self.batch and 0 <= row < len(self.batch.candidates):
            self.selected = row
            candidate = self.batch.candidates[row]
            self.editor.setPlainText(candidate["text"])
            labels = {"LOW": "低风险", "MEDIUM": "中风险", "HIGH": "高风险"}
            self.risk.setText(
                f"{labels[candidate['risk']]}  ·  {self.batch.scene}\n{self.batch.reason}"
            )
        self.update_actions()

    def copy_reply(self):
        text = self.editor.toPlainText()
        if not text.strip():
            return
        QApplication.clipboard().setText(text)
        if self.batch:
            self.db.feedback(self.batch.draft_ids[self.selected], text, "copied")
        self.notice.setText("已复制。复制不代表已发送，不会作为本人已发送语料学习。")

    def _manual_batch(self, text):
        """把输入框里手打的内容包成草稿，让它和候选走同一条发送闸门。

        不另开发送通道是有意的：确认弹窗、送达回执、重复发送拦截都挂在
        deliver 上，绕过去就等于手工发送没有任何保护。
        """
        snapshot = getattr(self, "snapshot", None)
        if self.contact_id is None or snapshot is None:
            self.notice.setText("请先读取当前会话，手工输入的内容才知道发给谁。")
            return None
        try:
            batch = self.engine.manual_draft(self.contact_id, text, snapshot)
        except Exception as exc:
            self.notice.setText(f"无法发送：{exc}")
            return None
        self.batch = batch
        self.selected = 0
        return batch

    def deliver(self, action, automatic=False, fresh=False):
        if self.busy:
            return
        text = self.editor.toPlainText().strip()
        if not text:
            return
        # 没有候选（或候选被判定为不该回复）时都是手打内容，补一份草稿再往下走。
        current = self.batch if (self.batch and self.batch.draft_ids) else None
        batch = current or self._manual_batch(text)
        if batch is None:
            return
        index = self.selected
        if batch.snapshot is None:
            self.notice.setText(
                "没有可用的会话快照，填入与发送已阻止。请先点一次读取当前会话。"
            )
            return
        readable = bool(getattr(batch.snapshot, "readable", True))
        # 读不到内容时只放行手打内容：屏幕是用户自己看的，由他判断回什么。
        # AI 候选依旧一律拦死——没读到上下文就生成的回复不该被发出去。
        if not readable and batch.scene != "手工":
            self.notice.setText(
                "会话内容未能读取，AI 候选不可发送。"
                "如确需回复，请在输入框里自己写，系统会在发送前再次确认。"
            )
            return
        if action == "send" and not automatic:
            name = batch.snapshot.contact_name if batch.snapshot else "未绑定真实聊天"
            warnings = []
            if not readable:
                warnings.append(
                    f"⚠ 系统读不到{batch.snapshot.platform}的聊天内容"
                    f"（{batch.snapshot.status}），无法核对聊天对象，"
                    "消息将发往该窗口当前打开的聊天。"
                )
            elif not getattr(batch.snapshot, "contact_verified", True):
                warnings.append(
                    f"⚠ 会话名读不出来，「{name}」是你在左栏选的，"
                    "系统未核实客户端此刻停在该聊天上。"
                )
            banner = ("\n".join(warnings) + "\n\n") if warnings else ""
            dialog = QMessageBox(
                QMessageBox.Question,
                "确认发送",
                f"{banner}发送给：{name}（{batch.snapshot.platform}）\n\n{text}\n\n"
                f"确认后将通过{batch.snapshot.platform}发送。",
                QMessageBox.Yes | QMessageBox.No,
                self,
            )
            dialog.setTextFormat(Qt.PlainText)
            dialog.setDefaultButton(QMessageBox.No)
            if dialog.exec() != QMessageBox.Yes:
                return
        self.notice.setText("正在校验联系人与聊天上下文…")

        def completed(status):
            self.notice.setText(
                {
                    "sent": f"{batch.snapshot.platform}消息回显已确认，已保存最终回复并更新风格。",
                    "filled": f"已填入{batch.snapshot.platform}输入框，尚未发送。"
                    "请核对后手动发送，或点击确认发送。",
                    "unknown": f"发送结果不确定，请在{batch.snapshot.platform}检查。系统不会自动重试。",
                }.get(status, status)
            )
            if status in {"sent", "unknown"}:
                self.batch = None
                self.snapshot = None
                self.clear_candidates()
            self.refresh_details()
            self.update_actions()

        self.run_task(
            lambda: self.engine.deliver(
                batch,
                index,
                text,
                self.adapter,
                action,
                confirmed=not automatic,
                automatic=automatic,
                fresh=fresh,
            ),
            completed,
        )

    def ignore(self):
        if self.batch:
            self.db.feedback(
                self.batch.draft_ids[self.selected], self.editor.toPlainText(), "ignored"
            )
        self.batch = None
        self.clear_candidates()
        self.update_actions()
        self.notice.setText("已忽略本次建议。")

    def mode_changed(self, mode):
        self.gate.auto_enabled = mode == "自动回复"
        if self.gate.auto_enabled:
            self.baselines.clear()
            self.listen.setChecked(True)
            self.notice.setText(
                "自动回复已开启：仅处理建立监听基线之后的新低风险文本，历史不会自动发送。"
            )
        else:
            self.notice.setText("当前模式：" + mode)
        platform = getattr(self, "current_platform", None) or self.adapter.get_platform_name()
        connected = getattr(self, "platform_connected", False)
        self.platform_status.setText(
            f"{platform}：{'已连接' if connected else '未连接'}  ·  "
            f"自动回复：{'开启' if self.gate.auto_enabled else '关闭'}"
        )

    def toggle_listen(self, enabled):
        if enabled:
            self.baselines.clear()
            self.timer.start()
        else:
            self.timer.stop()
            self.gate.auto_enabled = False
            if self.mode.currentText() == "自动回复":
                self.mode.setCurrentIndex(0)

    def stop(self):
        self.gate.stop()
        self.epoch += 1
        self.timer.stop()
        self.listen.setChecked(False)
        self.mode.setCurrentIndex(0)
        self.batch = None
        self.snapshot = None
        self.clear_candidates()
        self.pause.setText("恢复 AI")
        self.notice.setText(
            "已紧急停止：自动回复关闭，在途结果不会触发新操作。已执行的外部发送无法撤回。"
        )
        self.update_actions()
        log.info("紧急停止")

    def toggle_pause(self):
        if self.gate.stopped.is_set():
            if self.busy:
                self.notice.setText("后台请求正在结束，结束后可以恢复。AI 结果会被丢弃。")
                return
            self.gate.stopped.clear()
            self.pause.setText("暂停 AI")
            self.notice.setText("AI 已恢复，自动回复仍关闭。")
            self.update_actions()
        else:
            self.gate.stop()
            self.epoch += 1
            self.timer.stop()
            self.listen.setChecked(False)
            self.mode.setCurrentIndex(0)
            self.pause.setText("恢复 AI")
            self.notice.setText(
                "AI 已暂停，当前草稿保留。在途结果会被丢弃；恢复后仍需核对微信上下文。"
            )
            self.update_actions()
            log.info("暂停 AI，保留草稿")

    def update_actions(self):
        active = not self.busy and not self.gate.stopped.is_set()
        # 输入框里有字就算有回复：手打和选候选走的是同一条发送链路。
        has_reply = bool(self.editor.toPlainText().strip())
        # 读取结果不可信时，整条生成/填入/发送链路都要断开。
        snapshot = getattr(self, "snapshot", None)
        snapshot_ok = snapshot is None or bool(getattr(snapshot, "readable", True))
        batch_snapshot = self.batch.snapshot if self.batch else None
        target = batch_snapshot if batch_snapshot is not None else snapshot
        # AI 候选必须有可信快照；手打内容只要求窗口已定位——读不到内容的客户端
        # （钉钉这类聊天区是 CEF WebView 的）否则连手动发一句话都做不到，
        # 而那句话本来就是用户自己看着屏幕写的。
        has_candidate = bool(
            self.batch and self.batch.draft_ids and self.batch.scene != "手工"
        )
        deliverable = (
            self.contact_id is not None
            and target is not None
            and (bool(getattr(target, "readable", True)) or not has_candidate)
        )
        for widget in (
            self.contacts,
            self.search,
            self.settings_page,
            self.contact_page,
            self.memory_page,
            self.incoming,
            self.mode,
            self.listen,
            self.new_contact,
            self.import_btn,
            self.tree_btn,
        ):
            widget.setEnabled(active)
        self.read_btn.setEnabled(active)
        self.generate_btn.setEnabled(
            active
            and snapshot_ok
            and self.contact_id is not None
            and bool(self.incoming.toPlainText().strip())
        )
        self.copy_btn.setEnabled(has_reply and not self.busy)
        self.fill_btn.setEnabled(active and has_reply and deliverable)
        self.send_btn.setEnabled(active and has_reply and deliverable)
        self.ignore_btn.setEnabled(bool(self.batch) and not self.busy)

    def apply_settings(self, settings):
        self.settings = settings
        self.engine = create_engine(settings, self.gate)
        self.model_status.setText(
            "DeepSeek：已配置，待验证" if settings.api_key else "DeepSeek：未配置"
        )
        self.batch = None
        self.clear_candidates()
        self.update_actions()

    def memory_changed(self):
        self.batch = None
        self.clear_candidates()
        self.refresh_details()
        self.update_actions()

    def refresh_logs(self):
        path = ROOT / "logs/app.log"
        self.log_view.setPlainText(
            path.read_text(encoding="utf-8")[-30000:] if path.exists() else "暂无日志。"
        )

    def dump_tree(self):
        def saved(rows):
            path = ROOT / "data/uia-tree.json"
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            self.notice.setText(f"已导出脱敏控件结构：{path}")

        self.run_task(self.adapter.control_tree, saved)

    def import_chat(self):
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入聊天 JSON", str(ROOT), "JSON (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            name, messages = data["contact"], data["messages"]
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(messages, list)
                or len(messages) > 10000
            ):
                raise ValueError()
            for m in messages:
                if (
                    not isinstance(m, dict)
                    or m.get("sender") not in {"我", "对方"}
                    or not isinstance(m.get("content"), str)
                    or not m["content"].strip()
                ):
                    raise ValueError()
            from app.core.models import ChatMessage
            from hashlib import sha256

            digest = sha256(Path(path).read_bytes()).hexdigest()
            cid = self.db.ensure_contact(name, "手工")
            self.db.ingest(
                cid,
                tuple(
                    ChatMessage(
                        m["sender"],
                        m["content"],
                        f"import:{digest}:{i}",
                        timestamp=str(m.get("timestamp", "")),
                    )
                    for i, m in enumerate(messages)
                ),
            )
            self.contact_id = cid
            self.snapshot = None
            self.batch = None
            self.clear_candidates()
            self.incoming.clear()
            self.refresh_contacts()
            self.refresh_details()
            self.notice.setText("已导入本机手工会话，可用于学习表达风格。")
        except (ValueError, KeyError, TypeError, OSError):
            self.notice.setText(
                '导入失败。格式应为 {"contact":"姓名","messages":[{"sender":"我或对方","content":"内容"}]}。'
            )


def _set_contact_auto_reply(self, enabled):
    if not self.contact_id:
        self.contact_auto.setChecked(False)
        return
    self.db.set_auto_reply(self.contact_id, enabled)
    self.notice.setText(("已开启" if enabled else "已关闭") + "该联系人的自动回复；保持监听即可。")

WindowActions.set_contact_auto_reply = _set_contact_auto_reply
