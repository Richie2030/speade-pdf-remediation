# SPEADE in one page — for leadership

**The problem:** course PDFs — especially scanned ones — are often unreadable
by screen readers, which excludes students who rely on them. Making a PDF
accessible by hand in Adobe Acrobat takes skilled time per document; at course
scale it doesn't happen.

**What SPEADE does:** a small desktop application (built in-house, running
entirely on UCC machines) that does the mechanical 80% automatically — it
recognises the text of scans, works out headings, paragraphs, lists and
images, writes the accessibility structure into the PDF, and checks the
result against the international PDF/UA standard. Documents that pass need
minutes of human review instead of an hour of manual tagging.

**What it deliberately does not do:** it never publishes anything on its own.
Every document is reviewed by a trained Student Partner who sees exactly how
the document was tagged, fixes what machines can't judge (image descriptions,
tricky reading order) in Acrobat, and personally approves it. The software
prepares; a person decides — and every decision is permanently recorded (who,
when, and what the automated check said), so the university can evidence its
process.

**What it costs to run:** nothing recurring. No cloud services, no licences
beyond the Acrobat seats that exist anyway, no servers — the application and
all data live on the PCs it runs on. IT installs a signed folder plus four
standard open-source tools, once per machine.

**Privacy:** nothing leaves the machine. The only personal data recorded is
which reviewer approved which document, when — the accountability trail.

**Honest limits:** scan quality bounds recognition quality; complex layouts
and image descriptions still need the human step (by design); the tool is
finished software — v1 is the final version, frozen and documented, with no
ongoing development commitment.

**Where it stands:** built, tested (160+ automated tests plus scripted
browser testing and live pilots on real course documents), measured (roughly
15–40 seconds per document; a hundred-document batch runs unattended in about
an hour), and documented for reviewers, IT, and leadership. Remaining before
rollout: IT signing/allowlisting of the final build and a screen-reader spot
check on first approved documents.
