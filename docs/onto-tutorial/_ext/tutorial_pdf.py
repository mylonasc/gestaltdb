"""Tutorial-local vector SVG conversion for LaTeX/PDF export."""

import os

from sphinx.transforms.post_transforms.images import ImageConverter


class TutorialSVGConverter(ImageConverter):
    conversion_rules = [("image/svg+xml", "application/pdf")]

    def is_available(self):
        return True

    def convert(self, source, destination):
        import cairosvg

        cairosvg.svg2pdf(url=os.fspath(source), write_to=os.fspath(destination))
        return True


def setup(app):
    app.add_post_transform(TutorialSVGConverter)
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}
