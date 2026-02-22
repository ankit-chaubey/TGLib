"""
Utility class to build Python source code with proper indentation.
"""


class SourceBuilder:
    """Builds indented Python source code line by line."""

    def __init__(self, file):
        self.file = file
        self.current_indent = 0
        self._on_newline = True

    def indent(self):
        self.current_indent += 1

    def dedent(self):
        self.current_indent = max(0, self.current_indent - 1)

    def end_block(self):
        self.dedent()

    def writeln(self, line='', *args, **kwargs):
        if line:
            formatted = line.format(*args, **kwargs) if (args or kwargs) else line
            prefix = '    ' * self.current_indent
            self.file.write(f'{prefix}{formatted}\n')
        else:
            self.file.write('\n')

    def write(self, text):
        self.file.write(text)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
