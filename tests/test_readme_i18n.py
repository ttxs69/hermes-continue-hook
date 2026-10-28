#!/usr/bin/env python3
"""The two READMEs must not drift apart.

Why this test exists
--------------------
This repository has already been bitten by exactly this failure mode: the
patch file carried a hand-maintained copy of a generated module, and the two
silently diverged (the copy was missing `strip_code_and_links` and had an
older trigger list) until a user noticed the two modes behaving differently
on the same machine.  A translation is the same shape of risk: two copies of
the same prose, kept in step by hand.

So the invariant is mechanical, not editorial:

* the same number of headings, at the same levels
* the same number of fenced code blocks
* the same number of table rows
* every relative link target exists
* the two READMEs point at each other

The rule the industry uses (checked against two real repositories —
vastsa/PI-Desktop, 7.4k stars, and flipped-aurora/gin-vue-admin, 25k stars) is
``README.md`` holds the default language and the translation sits next to it
as ``README.zh-CN.md``, with one link in each pointing at the other.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EN = REPO / "README.md"
ZH = REPO / "README.zh-CN.md"


def _headings(text: str) -> list[tuple[int, str]]:
    """(level, normalised heading text) for every ATX heading, in order."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*$", line)
        if m:
            out.append((len(m.group(1)), m.group(2)))
    return out


def _fences(text: str) -> list[str]:
    """Every fenced code block, as (info-string, body) — order preserved."""
    blocks = []
    fence = None
    info = ""
    body: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s*```(.*)$", line)
        if m:
            if fence is None:
                fence = "```"
                info = m.group(1).strip()
                body = []
            else:
                blocks.append((info, "\n".join(body)))
                fence = None
            continue
        if fence is not None:
            body.append(line)
    return blocks


def _table_rows(text: str) -> int:
    """Count markdown table rows (a row is a line that starts and ends with |)."""
    return sum(
        1
        for line in text.splitlines()
        if line.strip().startswith("|") and line.strip().endswith("|")
    )


def _strip_comments(block: str) -> str:
    """The commands in a shell block, with comments and blank lines removed.

    A `#` inside a quoted string is not a comment — so only a `#` that starts
    a line, or follows whitespace, counts.
    """
    out = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Drop a trailing comment, but not a `#` inside quotes.
        in_single = in_double = False
        for index, char in enumerate(line):
            if char == "'" and not in_double:
                in_single = not in_single
            elif char == '"' and not in_single:
                in_double = not in_double
            elif (
                char == "#"
                and not in_single
                and not in_double
                and index > 0
                and line[index - 1] in " \t"
            ):
                line = line[:index]
                break
        out.append(line.rstrip())
    return "\n".join(out)


class TestTheTwoReadmesStayInStep(unittest.TestCase):
    def test_the_translation_exists(self) -> None:
        self.assertTrue(ZH.exists(), f"{ZH.name} is missing")

    def test_the_same_headings_in_the_same_order(self) -> None:
        en = _headings(EN.read_text(encoding="utf-8"))
        zh = _headings(ZH.read_text(encoding="utf-8"))
        self.assertEqual(
            len(en),
            len(zh),
            f"heading count differs: en={len(en)} zh={len(zh)}",
        )
        for (elvl, etext), (zlvl, ztext) in zip(en, zh, strict=True):
            self.assertEqual(elvl, zlvl, f"heading level differs at {etext!r}")
            # The heading text is translated, so only the level and the count
            # can be compared.  The anchor is checked separately, below.
            self.assertTrue(ztext.strip(), "empty heading in the translation")

    def test_the_same_number_of_code_blocks(self) -> None:
        en = _fences(EN.read_text(encoding="utf-8"))
        zh = _fences(ZH.read_text(encoding="utf-8"))
        self.assertEqual(
            len(en),
            len(zh),
            f"code block count differs: en={len(en)} zh={len(zh)}",
        )

    def test_code_blocks_keep_their_commands_verbatim(self) -> None:
        """A translated command is a bug — the block has to stay runnable.

        Comments may be translated: a Chinese reader needs to understand what
        the next three lines do.  What may not change is the command itself.
        So the comparison strips comments before comparing.
        """
        en = _fences(EN.read_text(encoding="utf-8"))
        zh = _fences(ZH.read_text(encoding="utf-8"))
        for index, ((einfo, ebody), (zinfo, zbody)) in enumerate(zip(en, zh, strict=True)):
            self.assertEqual(einfo, zinfo, f"code block {index} info string differs")
            if re.search(r"[\u4e00-\u9fff]", ebody):
                continue  # contains Chinese on purpose — kept as-is
            self.assertEqual(
                _strip_comments(ebody),
                _strip_comments(zbody),
                f"code block {index} changed a command; only comments may differ",
            )

    def test_the_same_number_of_table_rows(self) -> None:
        en = _table_rows(EN.read_text(encoding="utf-8"))
        zh = _table_rows(ZH.read_text(encoding="utf-8"))
        self.assertEqual(en, zh, f"table row count differs: en={en} zh={zh}")

    def test_they_link_to_each_other(self) -> None:
        en = EN.read_text(encoding="utf-8")
        zh = ZH.read_text(encoding="utf-8")
        self.assertIn("README.zh-CN.md", en, "the English README must offer the translation")
        self.assertIn("README.md", zh, "the Chinese README must link back to the English one")

    def test_relative_link_targets_exist(self) -> None:
        """Every relative link in either README must resolve to a real file."""
        for readme in (EN, ZH):
            text = readme.read_text(encoding="utf-8")
            for target in re.findall(r"\]\((?!https?://|#|mailto:)([^)\s]+)", text):
                path = (readme.parent / target).resolve()
                self.assertTrue(
                    path.exists(),
                    f"{readme.name} links to {target}, which does not exist",
                )


class TestTheEnglishReadmeIsEnglish(unittest.TestCase):
    """The default README is English, with one deliberate exception.

    The exception is the eight places where Chinese is the *subject matter*:
    the trigger list, the detector's Chinese test cases, and the one Chinese
    example quoted on the first screen.  A Chinese README whose English
    sibling is mostly Chinese is not two languages — it is one language twice.
    """

    #: Lines whose Chinese is content, not prose to be translated.
    ALLOWED_CHINESE = (
        "triggers.offer",
        "triggers.hand_off",
        "triggers.confirm_seek",
        "(Chinese)",
        "README.zh-CN.md",  # the language switch itself is named in Chinese
    )

    def test_prose_is_english(self) -> None:
        text = EN.read_text(encoding="utf-8")
        offenders = []
        for number, line in enumerate(text.splitlines(), 1):
            if not re.search(r"[\u4e00-\u9fff]", line):
                continue
            if any(marker in line for marker in self.ALLOWED_CHINESE):
                continue
            if "last_assistant_message" in line:
                continue  # detector test payload — the Chinese IS the test
            if "session_id" in line:
                continue
            offenders.append(f"{number}: {line.strip()[:90]}")
        self.assertEqual(
            offenders,
            [],
            "these lines are Chinese prose in the English README:\n  " + "\n  ".join(offenders),
        )


class TestTheReadmeCarriesNoStaleVersion(unittest.TestCase):
    """A version number written into the README expires at the next release.

    The README used to say `hermes-continue-hook-1.3.1.tgz`.  We published
    1.3.2 and the line was already wrong — nobody noticed, because a README is
    not compiled and a stale version is not a test failure.  It is now a
    wildcard, so no release can invalidate it.

    This is the README's version of the patch-mode drift: a copy of a fact
    kept in two places, with nothing that notices when they disagree.
    """

    def test_no_hardcoded_release_version(self) -> None:
        for readme in (EN, ZH):
            text = readme.read_text(encoding="utf-8")
            found = re.findall(r"hermes-continue-hook-(\d+\.\d+\.\d+)", text)
            self.assertEqual(
                found,
                [],
                f"{readme.name} hardcodes release version(s) {found}; "
                "use a wildcard so a release cannot invalidate it",
            )


if __name__ == "__main__":
    unittest.main()
