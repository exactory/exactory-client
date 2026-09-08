"""Locate HTML visual resources without fetching, rendering or executing code.

An anchor inside a figure, table, picture, SVG or MathML element selects its
whole enclosing visual. All static image candidates within that boundary are
retained, including srcset alternatives. Unsupported dynamic/embedded material
stays pending. A caption cannot hide its enclosing figure's image references.
Document stylesheets stay pending without a CSS cascade evaluator. Inline
ancestor style resources are retained from the complete saved HTML context.
"""

import base64
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import unquote_to_bytes, urldefrag, urljoin

from .errors import ResearchError
from .http import safe_url
from .jpeg import validate_jpeg
from .raster_containers import validate_gif, validate_png, validate_webp


VISUAL_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_URL = re.compile(r"url\(\s*['\"]?([^)'\"]+)['\"]?\s*\)", re.I)
_STATIC_FUNCTIONS = {"url", "rgb", "rgba", "hsl", "hsla", "matrix", "translate", "scale", "rotate", "skewx", "skewy"}
_SVG_DYNAMIC = {"script", "foreignobject", "style", "animate", "animatetransform", "animatemotion", "set", "discard"}


def _unsupported_style(value):
    return ("\\" in value or "@" in value or any(function.lower() not in _STATIC_FUNCTIONS
            for function in re.findall(r"([A-Za-z_-]+)\s*\(", value)))


def validate_visual(data, media_type):
    """Validate a supported byte container; this does not prove its meaning."""
    valid = False
    if media_type == "image/png":
        validate_png(data)
        valid = True
    elif media_type == "image/jpeg":
        validate_jpeg(data)
        valid = True
    elif media_type == "image/gif":
        validate_gif(data)
        valid = True
    elif media_type == "image/webp":
        validate_webp(data)
        valid = True
    elif media_type == "image/svg+xml":
        try:
            content = data.decode("utf-8-sig")
            if ("\x00" in content or "<!DOCTYPE" in content.upper() or "<!ENTITY" in content.upper()
                    or re.search(r"<\?(?!xml\s)", content, re.I)):
                raise ValueError()
            root = ET.fromstring(content)
            valid = root.tag in ("svg", "{http://www.w3.org/2000/svg}svg")
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1].lower() in _SVG_DYNAMIC:
                    raise ResearchError("visual_asset_dependencies_unsupported", "Dynamic SVG content requires another supported original representation")
                for key, value in node.attrib.items():
                    local = key.rsplit("}", 1)[-1].lower()
                    if local.startswith("on") or local == "base" or "\\" in value:
                        raise ResearchError("visual_asset_dependencies_unsupported", "Escaped, based or dynamic SVG dependencies are outside the supported subset")
                    if key.rsplit("}", 1)[-1] in ("href", "src") and not value.startswith("#"):
                        raise ResearchError("visual_asset_dependencies_unsupported", "External SVG resources are not self-contained visual bytes")
                style = " ".join(node.attrib.values())
                if _unsupported_style(style) or any(not v.strip().startswith("#") for v in _URL.findall(style)):
                    raise ResearchError("visual_asset_dependencies_unsupported", "External SVG style resources remain unsupported")
        except (ET.ParseError, ValueError) as error:
            raise ResearchError("malformed_visual_asset", "The saved SVG is malformed") from error
    elif media_type not in VISUAL_TYPES:
        raise ResearchError("unsupported_visual_asset", "Use a supported original image representation")
    if not valid:
        raise ResearchError("malformed_visual_asset", "The response does not contain a complete supported image container")


class _VisualMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.content, self.nodes, self.stack = content, [], []
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer("\n", content))

    def character_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        start = self.character_offset()
        end = start + len(self.get_starttag_text())
        node = {"tag": tag, "attrs": attrs, "start": start, "end": end if tag in _VOID else len(self.content), "closed": tag in _VOID,
                "in_svg": tag == "svg" or bool(self.stack and self.stack[-1]["in_svg"])}
        self.nodes.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            node = self.stack.pop()
            node.update(end=node["start"] + len(self.get_starttag_text()), closed=True)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                end = self.content.find(">", self.character_offset()) + 1
                for node in self.stack[index:]:
                    node.update(end=end, closed=True)
                del self.stack[index:]
                break


def resource_inventory(content, source_url, anchor):
    parser = _VisualMarkup(content)
    parser.feed(content)
    parser.close()
    start, end = anchor["start"], anchor["end"]
    containers = [n for n in parser.nodes if (n["tag"] in ("figure", "table", "picture", "svg", "math")
        or set((dict(n["attrs"]).get("class") or "").lower().split()) & {"ltx_figure", "figure", "ltx_table", "ltx_equation"})
        and n["start"] < end and start < n["end"]]
    # Enclosing figures take precedence over their internal picture/SVG nodes.
    enclosing = [n for n in containers if n["start"] <= start and end <= n["end"]]
    if enclosing:
        figures = [n for n in enclosing if n["tag"] == "figure" or "figure" in (dict(n["attrs"]).get("class") or "")]
        selected = min(figures or enclosing, key=lambda n: n["end"] - n["start"])
        start, end = selected["start"], selected["end"]
    selected_nodes = [n for n in parser.nodes if start <= n["start"] < end]
    selected_starts = {n["start"] for n in selected_nodes}
    pending, resources = [], {}
    if not containers and not any(n["tag"] in ("img", "image", "object", "embed") for n in selected_nodes):
        pending.append({"code": "visual_scope_unresolved"})
    base = source_url
    for node in parser.nodes:
        if node["tag"] == "base" and dict(node["attrs"]).get("href"):
            base = urljoin(source_url, dict(node["attrs"])["href"])
            break

    def resource(raw, node, attribute):
        if not raw:
            pending.append({"code": "visual_resource_missing", "attribute": attribute})
            return
        if raw.startswith("#"):
            # A referenced definition elsewhere in the HTML can itself depend
            # on uncaptured resources. No transitive renderer is implemented.
            pending.append({"code": "embedded_visual_dependency_unsupported"})
            return
        if raw.startswith("data:"):
            try:
                header, encoded = raw[5:].split(",", 1)
                data = base64.b64decode(encoded, validate=True) if header.endswith(";base64") else unquote_to_bytes(encoded)
                validate_visual(data, header.split(";", 1)[0])
            except (ValueError, ResearchError):
                pending.append({"code": "embedded_visual_unsupported"})
            return
        try:
            url = safe_url(urldefrag(urljoin(base, raw))[0])
        except ResearchError:
            pending.append({"code": "visual_url_unsupported", "attribute": attribute})
            return
        resources.setdefault(url, []).append({"element_start": node["start"], "attribute": attribute, "value": raw})

    # Document rules can select a visual from outside its anchor. Without a CSS
    # cascade evaluator, keep those rules pending even when literal URLs can be
    # retained. Ancestor inline styles also affect the selected visual surface.
    ancestors = [n for n in parser.nodes if n["start"] < start and end <= n["end"]]
    for node in parser.nodes:
        attrs = dict(node["attrs"])
        stylesheet = node["tag"] == "link" and "stylesheet" in (attrs.get("rel") or "").lower().split()
        if node["tag"] == "style" or stylesheet:
            item = {"code": "visual_document_style_unsupported", "element_start": node["start"]}
            if stylesheet:
                item["href"] = attrs.get("href")
            pending.append(item)
            if node["tag"] == "style" and node["start"] not in selected_starts:
                for raw in _URL.findall(content[node["start"]:node["end"]]):
                    resource(raw.strip(), node, "style")
    for node in selected_nodes + ancestors:
        attrs, tag = dict(node["attrs"]), node["tag"]
        in_visual, in_svg = node["start"] in selected_starts, node["in_svg"]
        if len(attrs) != len(node["attrs"]) or not node["closed"]:
            pending.append({"code": "visual_markup_ambiguous"})
        if tag in ("script", "canvas", "iframe", "video", "audio") or in_svg and tag in _SVG_DYNAMIC or any(k.startswith("on") for k in attrs):
            pending.append({"code": "dynamic_visual_unsupported"})
        if in_visual and tag == "img":
            if "src" in attrs:
                resource(attrs["src"], node, "src")
            elif "srcset" not in attrs:
                pending.append({"code": "visual_resource_missing", "attribute": "src"})
        if in_visual and tag in ("image", "use"):
            resource(attrs.get("href", attrs.get("xlink:href")), node, "href")
        elif in_visual and in_svg:
            for key, value in attrs.items():
                if key.rsplit(":", 1)[-1] in ("href", "src"):
                    resource(value, node, key)
        if in_visual and tag in ("object", "embed"):
            resource(attrs.get("data" if tag == "object" else "src"), node, "data" if tag == "object" else "src")
        if in_visual and "srcset" in attrs:
            if not attrs["srcset"] or "data:" in attrs["srcset"]:
                pending.append({"code": "visual_srcset_unsupported"})
            else:
                for candidate in attrs["srcset"].split(","):
                    parts = candidate.split()
                    if not parts or len(parts) > 2 or len(parts) == 2 and not re.fullmatch(r"(?:[0-9]+w|[0-9]*\.?[0-9]+x)", parts[1]):
                        pending.append({"code": "visual_srcset_unsupported"})
                    else:
                        resource(parts[0], node, "srcset")
        style = attrs.get("style") or ""
        if tag == "style":
            style += content[node["start"]:node["end"]]
        presentation = " ".join(v or "" for k, v in attrs.items() if in_svg or k in
                                ("fill", "stroke", "filter", "clip-path", "mask", "marker", "marker-start", "marker-mid", "marker-end"))
        style += " " + presentation
        if _unsupported_style(style) or any(k.rsplit(":", 1)[-1] == "base" for k in attrs):
            pending.append({"code": "visual_style_unsupported"})
        for raw in _URL.findall(style):
            resource(raw.strip(), node, "style")
    return {"start": start, "end": end, "resources": [{"url": url, "references": references} for url, references in sorted(resources.items())],
            "pending": pending}
