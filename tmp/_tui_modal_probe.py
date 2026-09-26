"""Probe: 在 ModalScreen 中，App 级 priority 绑定与模态级绑定谁先响应？"""
import asyncio

from textual.app import App
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static

EVENTS = []


class Sel(ModalScreen):
    BINDINGS = [
        Binding("pageup", "body_up", "up", priority=True),
        Binding("pagedown", "body_down", "down", priority=True),
        Binding("escape", "cancel", "cancel"),
    ]

    def compose(self):
        with Vertical(id="modal"):
            yield Static("PLAN\n" + ("line\n" * 200), id="body")
            yield OptionList("a", "b", "c", id="list")

    def on_mount(self):
        self.query_one("#list").focus()

    def action_body_up(self):
        EVENTS.append("MODAL pageup")
        self.query_one("#body").scroll_up()

    def action_body_down(self):
        EVENTS.append("MODAL pagedown")
        self.query_one("#body").scroll_down()

    def action_cancel(self):
        self.dismiss(None)


class MyApp(App):
    BINDINGS = [
        Binding("pageup", "log_up", "logup", priority=True),
        Binding("pagedown", "log_down", "logdown", priority=True),
    ]

    def action_log_up(self):
        EVENTS.append("APP pageup")

    def action_log_down(self):
        EVENTS.append("APP pagedown")

    def on_mount(self):
        self.push_screen(Sel())


async def main():
    app = MyApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("pageup")
        await pilot.pause()
        await pilot.press("pagedown")
        await pilot.pause()
        print("EVENTS:", EVENTS)
        print("active_bindings keys:", sorted(app.screen.active_bindings.keys())[:20])
        print("body scroll_y:", app.screen.query_one("#body").scroll_y)
        app.exit()


asyncio.run(main())
