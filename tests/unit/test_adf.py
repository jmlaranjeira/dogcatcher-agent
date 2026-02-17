"""Tests for the Markdown-to-ADF converter."""

import pytest

from agent.jira.adf import markdown_to_adf


class TestMarkdownToAdf:
    """Core conversion tests."""

    def test_empty_string(self):
        result = markdown_to_adf("")
        assert result["type"] == "doc"
        assert result["version"] == 1
        assert len(result["content"]) == 1

    def test_plain_text(self):
        result = markdown_to_adf("Hello world")
        assert result["content"] == [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Hello world"}],
            }
        ]

    def test_heading_level_3(self):
        result = markdown_to_adf("### Problem summary")
        block = result["content"][0]
        assert block["type"] == "heading"
        assert block["attrs"]["level"] == 3
        assert block["content"][0]["text"] == "Problem summary"

    def test_heading_level_2(self):
        result = markdown_to_adf("## Section")
        block = result["content"][0]
        assert block["type"] == "heading"
        assert block["attrs"]["level"] == 2

    def test_horizontal_rule(self):
        result = markdown_to_adf("---")
        assert result["content"] == [{"type": "rule"}]

    def test_long_rule(self):
        result = markdown_to_adf("-----")
        assert result["content"] == [{"type": "rule"}]

    def test_bullet_dash(self):
        result = markdown_to_adf("- Item one\n- Item two")
        bl = result["content"][0]
        assert bl["type"] == "bulletList"
        assert len(bl["content"]) == 2
        assert bl["content"][0]["type"] == "listItem"
        assert bl["content"][0]["content"][0]["content"][0]["text"] == "Item one"

    def test_bullet_asterisk(self):
        result = markdown_to_adf("* Item")
        assert result["content"][0]["type"] == "bulletList"

    def test_bullet_unicode(self):
        """Datadog links use unicode bullet."""
        result = markdown_to_adf("\u2022 Link text")
        assert result["content"][0]["type"] == "bulletList"

    def test_bold_text(self):
        result = markdown_to_adf("This is **bold** text")
        nodes = result["content"][0]["content"]
        # Should have: "This is ", bold "bold", " text"
        bold_node = [n for n in nodes if n.get("marks")]
        assert len(bold_node) == 1
        assert bold_node[0]["text"] == "bold"
        assert bold_node[0]["marks"] == [{"type": "strong"}]

    def test_url_link(self):
        result = markdown_to_adf("Visit https://example.com/page for details")
        nodes = result["content"][0]["content"]
        link_node = [
            n for n in nodes if any(m.get("type") == "link" for m in n.get("marks", []))
        ]
        assert len(link_node) == 1
        assert link_node[0]["text"] == "https://example.com/page"
        assert link_node[0]["marks"][0]["attrs"]["href"] == "https://example.com/page"

    def test_multiline_paragraph(self):
        """Consecutive plain lines should join with hardBreak."""
        result = markdown_to_adf("Line one\nLine two\nLine three")
        para = result["content"][0]
        assert para["type"] == "paragraph"
        hard_breaks = [n for n in para["content"] if n.get("type") == "hardBreak"]
        assert len(hard_breaks) == 2

    def test_blank_line_splits_paragraphs(self):
        result = markdown_to_adf("Para one\n\nPara two")
        assert len(result["content"]) == 2
        assert all(b["type"] == "paragraph" for b in result["content"])


class TestRealDescription:
    """Test with content matching actual LLM + build_enhanced_description output."""

    SAMPLE = (
        "### Problem summary\n"
        "The system encountered an error while sending an email via SES.\n"
        "\n"
        "### Possible Causes\n"
        "- The email address has not been verified in SES.\n"
        "- Region mismatch between SES setup and sending request.\n"
        "\n"
        "### Suggested Actions\n"
        "- Verify the email address in the SES console.\n"
        "- Check SES sending permissions.\n"
        "\n"
        "### Severity\n"
        "high\n"
        "\n"
        "---\n"
        "\U0001f552 Timestamp: 2026-02-16T08:37:26.598Z\n"
        "\U0001f9e9 Logger: unknown.logger\n"
        "\U0001f9f5 Thread: unknown.thread\n"
        "\U0001f4dd Original Log: Error sending email via SES\n"
        "\U0001f50d Detail: no detailed log\n"
        "\U0001f4c8 Occurrences in last 48h: 1\n"
        "---\n"
        "\U0001f517 Datadog Links:\n"
        "\u2022 Similar Errors: https://app.datadoghq.eu/logs?query=test\n"
    )

    def test_structure(self):
        result = markdown_to_adf(self.SAMPLE)
        types = [b["type"] for b in result["content"]]
        # Should contain headings, paragraphs, bullets, rules
        assert "heading" in types
        assert "bulletList" in types
        assert "rule" in types
        assert "paragraph" in types

    def test_heading_count(self):
        result = markdown_to_adf(self.SAMPLE)
        headings = [b for b in result["content"] if b["type"] == "heading"]
        assert (
            len(headings) == 4
        )  # Problem summary, Possible Causes, Suggested Actions, Severity

    def test_bullet_lists(self):
        result = markdown_to_adf(self.SAMPLE)
        bullet_lists = [b for b in result["content"] if b["type"] == "bulletList"]
        # Two markdown bullet lists + one unicode bullet list (Datadog links)
        assert len(bullet_lists) == 3

    def test_rules(self):
        result = markdown_to_adf(self.SAMPLE)
        rules = [b for b in result["content"] if b["type"] == "rule"]
        assert len(rules) == 2

    def test_datadog_link_is_clickable(self):
        result = markdown_to_adf(self.SAMPLE)
        # Find the bulletList containing the Datadog link
        for block in result["content"]:
            if block["type"] == "bulletList":
                for item in block["content"]:
                    para = item["content"][0]
                    for node in para["content"]:
                        marks = node.get("marks", [])
                        link_marks = [m for m in marks if m["type"] == "link"]
                        if link_marks:
                            assert "datadoghq.eu" in link_marks[0]["attrs"]["href"]
                            return
        pytest.fail("No clickable Datadog link found")
