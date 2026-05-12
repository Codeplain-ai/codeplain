from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Label, ListItem, ListView, Static

import plain_spec
from partial_rendering import PartialRender, PartialRenderChoice
from plain_modules import PlainModule
from tui.components import CustomFooter, TUIComponents


class PartialRenderTUI(App):
    BINDINGS = [
        Binding("ctrl+o", "toggle_expand", "Expand/Collapse", show=False),
        Binding("ctrl+c", "copy_selection", "Copy", show=False),
        Binding("ctrl+d", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        plain_module: PlainModule,
        partial_render: PartialRender,
        choices: dict[str, PartialRenderChoice],
        state_machine_version: str,
        render_id: str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.plain_module = plain_module
        self.partial_render = partial_render
        self.state_machine_version = state_machine_version
        self.render_id = render_id
        self._expandable_labels: list[dict] = []
        self.choices = choices

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
        pr = self.partial_render

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
            change_box.mount(
                Label(
                    f"--- {title_start} detected in required module [#5593FF]{pr.change.module_name}[/] ---",
                    classes="rendering-info-row",
                )
            )
            change_box.mount(
                Label(
                    f"{title_start} in a required module may affect the current module", classes="rendering-info-title"
                )
            )

        elif pr.last_render_module.is_module_fully_rendered():
            change_box.mount(Label("--- Rendering interrupted ---", classes="rendering-info-row"))
            change_box.mount(Label("The current module was fully rendered.", classes="rendering-info-title"))
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
        self.mount(Label("How would you like to continue?", classes="partial-render-question"), before=lv)
        for key, choice in self.choices.items():
            lv.append(ListItem(Label(f"[bold]{key}.[/bold] {choice.msg}"), id=f"choice-{key}"))
        lv.focus()

    def _register_expandable(self, label: Label, prefix: str, full_text: str) -> None:
        first_line = full_text[:100]
        short = f"{prefix} {first_line} [#888](ctrl+o to expand)[/]"
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
        self.selected_choice = self.choices[key]
        self.exit(self.selected_choice)

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
