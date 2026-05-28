from typing import Callable, Optional

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Label, ListItem, ListView, Static

import plain_spec
from partial_rendering import PlainModuleRenderState, RenderChoice, get_all_affected_modules_from_change
from plain_modules import PlainModule
from tui.components import CustomFooter, TUIComponents


class PlainModuleRenderChoiceTUI(App):
    BINDINGS = [
        Binding("ctrl+o", "toggle_expand", "Expand/Collapse", show=False),
        Binding("ctrl+c", "copy_selection", "Copy", show=False),
        Binding("ctrl+d", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        plain_module: PlainModule,
        plain_module_render_state: PlainModuleRenderState,
        render_choices: dict[str, RenderChoice],
        state_machine_version: str,
        render_id: str,
        on_cancel: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.plain_module = plain_module
        self.plain_module_render_state = plain_module_render_state
        self.state_machine_version = state_machine_version
        self.render_id = render_id
        self._expandable_labels: list[dict] = []
        self.render_choices = render_choices
        self._on_cancel = on_cancel

    def get_msg_from_choice(self, render_choice: RenderChoice) -> str:
        if render_choice.choice_type == "module_start":
            if render_choice.module.module_name == self.plain_module.module_name:
                if render_choice.module.is_module_fully_rendered():
                    return f"Re-render the current module ([#5593FF]{render_choice.module.module_name}[/])"
                else:
                    return f"Start rendering the current module ([#5593FF]{render_choice.module.module_name}[/])"
            else:
                return f"Start from module [#5593FF]{render_choice.module.module_name}[/]"
        elif render_choice.choice_type == "rerender_affected" and self.plain_module_render_state.change is not None:
            all_affected_modules = get_all_affected_modules_from_change(
                self.plain_module, self.plain_module_render_state
            )

            return f"Re-render all affected modules ([#5593FF]{', '.join([m.module_name for m in all_affected_modules])}[/])"

        elif render_choice.choice_type == "rerender_from_first":
            return f"Re-render from first module ([#5593FF]{render_choice.module.module_name}[/])"
        elif render_choice.choice_type == "continue_from_frid":
            msg = "Continue from"
            next_frid = render_choice.render_range[0] if render_choice.render_range else None
            if next_frid != plain_spec.get_first_frid(render_choice.module.plain_source):
                msg += (
                    f" functionality [#5593FF]{next_frid}[/] in module [#5593FF]{render_choice.module.module_name}[/]"
                )
            else:
                msg += f" module [#5593FF]{render_choice.module.module_name}[/]"
            return msg
        elif render_choice.choice_type == "quit":
            return "Quit"

    def compose(self) -> ComposeResult:
        with Vertical(id=TUIComponents.DASHBOARD_VIEW.value):
            with VerticalScroll():
                yield Static(
                    f"[#FFFFFF]*codeplain[/#FFFFFF] [#888888](v{self.state_machine_version})[/#888888]",
                    id="codeplain-header",
                    classes="codeplain-header",
                )
                yield Vertical(id="info-panel")
                yield ListView(id="choice-list")
        yield CustomFooter(render_id=self.render_id, use_logs_shortcut=False, use_pause_shortcut=False)

    def on_mount(self) -> None:
        pr = self.plain_module_render_state

        info_panel = self.query_one("#info-panel", Vertical)
        info_panel.mount(Label("module status", classes="rendering-info-title"))

        info_box = Vertical(classes="rendering-info-box")
        info_panel.mount(info_box)

        info_box.mount(Label(f"Module: {pr.last_render_module.module_name}", classes="rendering-info-row"))
        if pr.last_render_module.is_module_fully_rendered():
            info_box.mount(Label("Module fully rendered", classes="rendering-info-row"))
        elif pr.last_render_frid is not None:
            frid = pr.last_render_frid
            specifications, _ = plain_spec.get_specifications_for_frid(pr.last_render_module.plain_source, frid)
            functionality = specifications[plain_spec.FUNCTIONAL_REQUIREMENTS][-1]
            label = Label("", classes="rendering-info-row")
            info_box.mount(label)
            self._register_expandable(label, f"Functionality {frid}:", functionality)

        change_box = Vertical(classes="change-box")
        info_panel.mount(change_box)

        if pr.change:
            title_start = "Spec changes" if pr.change_type == "spec_change" else "Code changes"
            is_required_module = pr.change.module_name != self.plain_module.module_name
            change_box.mount(
                Label(
                    f"--- {title_start} detected in {'required ' if is_required_module else 'current '}module [#5593FF]{pr.change.module_name}[/] ---",
                    classes="rendering-info-row",
                )
            )
            if is_required_module:
                change_box.mount(
                    Label(
                        f"{title_start} in a required module may affect the current module",
                        classes="rendering-info-title",
                    )
                )

        elif pr.last_render_module.is_module_fully_rendered():
            change_box.mount(Label("The current module is fully rendered.", classes="rendering-info-title"))
        else:
            interrupted_frid = "1"
            interrupted_module = pr.last_render_module
            if pr.last_render_frid:
                next_frid, next_module = pr.last_render_module.get_next_frid(
                    pr.last_render_frid, pr.last_render_module.module_name
                )
                if next_frid is not None:
                    interrupted_frid = next_frid
                    interrupted_module = next_module

            msg = f"--- Rendering interrupted during [#5593FF]functionality {interrupted_frid}"
            if interrupted_module != pr.last_render_module:
                msg += f" of module {interrupted_module.module_name}[/] ---"
            else:
                msg += "[/] ---"

            change_box.mount(Label(msg, classes="rendering-info-row"))

            if pr.last_render_module.is_initial_module():
                change_box.mount(
                    Label(
                        "Resume from interrupted functionality.",
                        classes="rendering-info-title",
                    )
                )
            else:
                change_box.mount(
                    Label(
                        "Resume from the last successfully rendered functionality or start over.",
                        classes="rendering-info-title",
                    )
                )

        # Populate the ListView
        lv = self.query_one("#choice-list", ListView)
        self.mount(Label("How would you like to proceed?", classes="partial-render-question"), before=lv)
        for key, choice in self.render_choices.items():
            lv.append(ListItem(Label(f"[bold]{key}.[/bold] {self.get_msg_from_choice(choice)}"), id=f"choice-{key}"))
        lv.focus()

    def _register_expandable(self, label: Label, prefix: str, full_text: str) -> None:
        first_lines = "\n".join(full_text.splitlines()[:3])
        short = f"{prefix} {first_lines} [#888](ctrl+o to expand)[/]"
        full = f"{prefix} {full_text} [#888](ctrl+o to collapse)[/]"
        label.update(short)
        self._expandable_labels.append({"label": label, "short": short, "full": full, "expanded": False})

    def action_toggle_expand(self) -> None:
        for entry in self._expandable_labels:
            entry["expanded"] = not entry["expanded"]
            entry["label"].update(entry["full"] if entry["expanded"] else entry["short"])

    @on(ListView.Selected)
    def on_choice_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if not item_id or not item_id.startswith("choice-"):
            raise ValueError(f"Invalid item ID: {item_id}")
        key = item_id.split("-", 1)[1]
        self._select_choice(key)

    def on_key(self, event) -> None:
        """Allow selecting a choice by typing its number directly."""
        if event.key in self.render_choices:
            event.stop()
            event.prevent_default()
            self._select_choice(event.key)

    def _select_choice(self, key: str) -> None:
        self.selected_choice = self.render_choices[key]
        self.exit(self.selected_choice)

    def action_quit(self) -> None:
        if self._on_cancel:
            self._on_cancel()
        self.exit()

    async def action_copy_selection(self) -> None:
        """Handle ctrl+c: copy selected text if any.

        - If text is selected -> copy it to clipboard
        - If no text is selected -> do nothing
        """
        selected_text = self.screen.get_selected_text()
        if selected_text:
            self.copy_to_clipboard(selected_text)
            self.screen.clear_selection()
            self.notify("Copied to clipboard", timeout=2)
