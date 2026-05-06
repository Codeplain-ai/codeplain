from pathlib import Path
from typing import Mapping, Sequence, TextIO

from liquid2 import Environment, RenderContext, Template, TemplateNotFoundError
from liquid2.builtin import IncludeTag
from liquid2.builtin.tags.include_tag import IncludeNode


class Plain2CodeIncludeNode(IncludeNode):
    def render_to_output(self, context: RenderContext, buffer: TextIO) -> int:
        """Render the node to the output buffer."""
        name = self.name.evaluate(context)
        whitespaces = 0
        is_comment = False
        i = self.token.start
        while self.token.source[i] != "\n" and i >= 0:
            if self.token.source[i] == " ":
                whitespaces += 1
            elif self.token.source[i] == ">":
                is_comment = True
                break
            else:
                whitespaces = 0
            i -= 1

        if is_comment:
            return buffer.write(str(self))

        try:
            template = context.env.get_template(str(name), context=context, tag=self.tag, whitespaces=whitespaces)
        except TemplateNotFoundError as err:
            detail = str(err)
            long_message = f"""Template not found: {detail}
The required template could not be found. Templates are searched in the following order (highest to lowest precedence):

    1. The directory containing your .plain file
    2. The directory specified by --template-dir (if provided)
    3. The built-in 'standard_template_library' directory

Please ensure that the missing template exists in one of these locations, or specify the correct --template-dir if using custom templates.
"""
            wrapped = TemplateNotFoundError(long_message)
            wrapped.token = self.name.token
            wrapped.template_name = context.template.full_name()
            raise wrapped from err

        namespace: dict[str, object] = dict(arg.evaluate(context) for arg in self.args)

        character_count = 0

        with context.extend(namespace, template=template):
            if self.var:
                val = self.var.evaluate(context)
                key = self.alias or template.name.split(".")[0]

                if isinstance(val, Sequence) and not isinstance(val, str):
                    context.raise_for_loop_limit(len(val))
                    for itm in val:
                        namespace[key] = itm
                        character_count += template.render_with_context(context, buffer, partial=True)
                else:
                    namespace[key] = val
                    character_count = template.render_with_context(context, buffer, partial=True)
            else:

                character_count = template.render_with_context(context, buffer, partial=True)

        return character_count


class Plain2CodeIncludeTag(IncludeTag):
    node_class = Plain2CodeIncludeNode


class Plain2CodeLoaderMixin:
    def __init__(self, *args, **kwargs):
        if not hasattr(self, "get_source"):
            raise NotImplementedError("Class must implement get_source")
        super().__init__(*args, **kwargs)

    def load(
        self,
        env: Environment,
        name: str,
        *,
        globals: Mapping[str, object] | None = None,
        context: RenderContext | None = None,
        **kwargs: object,
    ) -> Template:
        """
        Find and parse template source code.

        Args:
            env: The `Environment` attempting to load the template source text.
            name: A name or identifier for a template's source text.
            globals: A mapping of render context variables attached to the
                resulting template.
            context: An optional render context that can be used to narrow the template
                source search space.
            kwargs: Arbitrary arguments that can be used to narrow the template source
                search space.
        """
        source, full_name, uptodate, matter = self.get_source(env, name, context=context, **kwargs)
        whitespaces = kwargs.get("whitespaces", 0)
        assert isinstance(whitespaces, int)
        source = source.rstrip().replace("\n", "\n" + " " * whitespaces)

        path = Path(full_name)

        template = env.from_string(
            source,
            name=path.name,
            path=path,
            globals=globals,
            overlay_data=matter,
        )

        template.uptodate = uptodate
        return template
