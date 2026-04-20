from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Label, ListItem, ListView

import plain_spec
from partial_rendering import PartialRender, PartialRenderChoice
from plain_modules import PlainModule


class PartialRenderTUI(App):
    def __init__(self, plain_module: PlainModule, partial_render: PartialRender, **kwargs):
        super().__init__(**kwargs)
        self.plain_module = plain_module
        self.partial_render = partial_render
        self.choices = {}  # populated in on_mount

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("[bold]--- Partial Render Detected ---[/bold]", classes="highlight"),
            Horizontal(
                Vertical(id="info-panel-left"),
                Vertical(id="info-panel-right"),
                id="info-panel-columns",
            ),
            id="info-panel",
        )
        yield ListView(id="choice-list")

    def on_mount(self) -> None:
        pr = self.partial_render
        pm = self.plain_module

        # Info labels — left side
        left = self.query_one("#info-panel-left", Vertical)
        left.mount(Label(f"[#e0ff6e]Current state:[/]"))
        left.mount(Label(f"Target module: [{'#79fc96'}]{pm.module_name}[/]"))
        if pr.module.is_module_fully_rendered():
            left.mount(Label(f"Last fully rendered module: [{'#79fc96'}]{pr.module.module_name}[/]"))
        else:
            left.mount(Label(f"Partially rendered module: [{'#79fc96'}]{pr.module.module_name}[/]"))
            left.mount(Label(f"Last fully rendered functionality: [{'#79fc96'}]{pr.frid}[/]"))

        left.mount(Label(f"Spec change:  [{'#79fc96'}]{'Yes' if pr.spec_change else 'No'}[/]"))
        left.mount(Label(f"Code change:  [{'#79fc96'}]{'Yes' if pr.code_change else 'No'}[/]"))

        # Build choices (same logic as original)
        choice_idx = 1
        if pr.frid is not None:
            next_frid, next_module = pm.get_next_frid(pr.frid, pr.module.module_name)
            functionality = next_module.plain_source[plain_spec.FUNCTIONAL_REQUIREMENTS][int(next_frid) - 1]
            print(functionality)
            # Placeholder — right side

            right = self.query_one("#info-panel-right", Vertical)
            right.mount(Label(f"[#e0ff6e]Next functionality:[/]"))
            right.mount(
                Label(f"Module [{'#79fc96'}]{next_module.module_name}[/], functionality [{'#79fc96'}]{next_frid}[/]")
            )
            right.mount(Label(functionality["markdown"]))

            msg = f"Continue from next functionality (module {next_module.module_name}"
            if next_frid != plain_spec.get_first_frid(next_module.plain_source):
                msg += f" functionality {next_frid})"
            else:
                msg += ")"
            self.choices[str(choice_idx)] = PartialRenderChoice(
                module=next_module,
                render_range=plain_spec.get_render_range_from(next_frid, next_module.plain_source),
                msg=msg,
            )
            choice_idx += 1

        if pr.spec_change:
            self.choices[str(choice_idx)] = PartialRenderChoice(
                module=pr.module,
                render_range=None,
                msg=f"Re-render {pr.module.module_name} from start (spec change)",
            )
            choice_idx += 1

        if pr.code_change:
            self.choices[str(choice_idx)] = PartialRenderChoice(
                module=pr.module,
                render_range=None,
                msg=f"Re-render {pr.module.module_name} from start (code change)",
            )
            choice_idx += 1

        first_module = pm.all_required_modules[0]
        self.choices[str(choice_idx)] = PartialRenderChoice(
            module=first_module,
            render_range=None,
            msg=f"Re-render all (start from: {first_module.module_name})",
        )
        choice_idx += 1

        self.choices[str(choice_idx)] = PartialRenderChoice(module=None, render_range=None, msg="Quit")

        # Populate the ListView
        lv = self.query_one("#choice-list", ListView)
        self.mount(Label("How would you like to start rendering?", classes="partial-render-question"), before=lv)
        for key, choice in self.choices.items():
            lv.append(ListItem(Label(f"[bold]{key}.[/bold]  {choice.msg}"), id=f"choice-{key}"))

    @on(ListView.Selected)
    def on_choice_selected(self, event: ListView.Selected) -> None:
        # Extract the key from the widget id ("choice-1" → "1")
        key = event.item.id.split("-", 1)[1]
        self.selected_choice = self.choices[key]
        self.exit(self.selected_choice)
