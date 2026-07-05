"""
S3 upload tool — uploads agent-generated artifacts (PDF / PPTX / DOCX / XLSX)
from the Python REPL session directory to iDrive e2 and returns a presigned
URL the user can click.

Adapted from Kroolo's enterprise-fastapi/utils/s3_upload_tool.py, retargeted
to PriceIQ's iDrive S3-compatible storage.

`build_s3_upload_tool(on_uploaded=...)` builds the tool with an optional post-upload
hook so a caller (e.g. the capture agent) can copy the SAME bytes to a second destination
— SharePoint — in the same pass, while the file is still on local disk, instead of
re-downloading it later. The default module-level `s3_upload_tool` has no hook.
"""

from agno.tools import tool
import os
import uuid
import logging
from typing import Any, Callable, Dict, Optional

from client.idrive_storage import get_idrive_storage
from utils.python_repl_tool import get_session_id, _session_temp_dirs

logger = logging.getLogger(__name__)

# A post-upload hook: (presigned_url, local_path, filename) -> None. Runs after a successful
# iDrive upload and BEFORE the local file is deleted. Best-effort — a raise is swallowed.
OnUploaded = Callable[[str, str, str], None]


def build_s3_upload_tool(on_uploaded: Optional[OnUploaded] = None):
    """Build the REPL→iDrive upload tool. If `on_uploaded` is given, it is called after a
    successful iDrive upload and BEFORE the local file is removed, so the caller can also send
    the same local bytes elsewhere (e.g. file the deliverable into SharePoint). The hook is
    wrapped best-effort — it can never fail the upload."""

    @tool(stop_after_tool_call=False)
    async def s3_upload_tool(filename: str, description: str = "") -> Dict[str, Any]:
        """
        📤 Upload a file generated in the Python REPL to iDrive cloud storage and
        return a shareable presigned URL.

        USE THIS after generating a PDF / PPTX / DOCX / XLSX / image / CSV in
        `python_repl_tool`. The user gets a clickable link to download the file.

        HOW IT WORKS:
        - The REPL writes files into a per-session temp directory.
        - This tool finds that file by name, uploads it to iDrive e2, and returns
          a presigned URL valid for 7 days.

        WORKFLOW:
        1. In `python_repl_tool`, generate the file with a simple filename
           (e.g. "pricing_summary.pdf"). Do NOT include any directory prefix.
        2. Call this tool with the same filename.
        3. Quote the returned `url` to the user as a markdown link.

        EXAMPLE — generate a PDF and upload:
        ```
        # Step 1 (python_repl_tool):
        from reportlab.pdfgen import canvas
        c = canvas.Canvas("pricing_summary.pdf")
        c.drawString(100, 750, "Pricing Summary")
        c.drawString(100, 730, f"Grand Total: $3,016,285.83")
        c.save()
        print("done")

        # Step 2 (s3_upload_tool):
        s3_upload_tool(filename="pricing_summary.pdf",
                       description="Generated pricing summary PDF")
        ```

        Args:
            filename: Name of the file in the REPL working directory (no path).
            description: Short past-tense description of what the file contains.

        Returns:
            Dict with `success`, `url` (presigned, 7-day), `filename`, `error`.
        """
        try:
            session_id = get_session_id()

            # Locate the session's temp dir (created by python_repl_tool).
            if session_id not in _session_temp_dirs:
                return {
                    "success": False,
                    "url": None,
                    "filename": filename,
                    "error": (
                        "No active REPL session — generate the file with "
                        "python_repl_tool first, then call this tool."
                    ),
                }

            working_dir = _session_temp_dirs[session_id].name
            local_path = os.path.join(working_dir, filename)

            if not os.path.isfile(local_path):
                files = os.listdir(working_dir) if os.path.isdir(working_dir) else []
                return {
                    "success": False,
                    "url": None,
                    "filename": filename,
                    "error": f"File '{filename}' not found in session dir. Available: {files}",
                }

            # Upload under a chat-artifacts namespace so we don't collide with
            # the user's actual proposal documents.
            storage = get_idrive_storage()
            artifact_id = uuid.uuid4().hex[:12]
            url, object_key = storage.upload_document(
                file_path=local_path,
                user_id="chat-artifacts",
                proposal_id=session_id,
                filename=f"{artifact_id}_{filename}",
            )

            logger.info(f"[s3_upload_tool] uploaded {filename} → {object_key}")

            # Second destination (e.g. SharePoint) — from the SAME local bytes, before cleanup.
            if on_uploaded is not None:
                try:
                    on_uploaded(url, local_path, filename)
                except Exception as hook_err:  # noqa: BLE001 — never fail the upload over a side-copy
                    logger.warning(f"[s3_upload_tool] on_uploaded hook failed: {hook_err}")

            # Clean up local file after successful upload to save disk space
            try:
                os.remove(local_path)
                logger.info(f"[s3_upload_tool] deleted local file: {local_path}")
            except Exception as cleanup_error:
                logger.warning(f"[s3_upload_tool] failed to delete local file: {cleanup_error}")

            return {
                "success": True,
                "url": url,
                "filename": filename,
                "object_key": object_key,
                "error": None,
                "note": (
                    "IMPORTANT — DO NOT output this URL as plain text in your reply. "
                    "The frontend automatically detects the URL from this tool result and "
                    "renders it as a styled download card with a clickable Download button. "
                    "If you paste the URL in your message, the user will see a raw link "
                    "instead of the rich UI component. "
                    "Your reply should only confirm the file is ready and briefly describe "
                    "its contents — for example: 'Your pricing summary is ready — it covers "
                    "the prime/sub split, year-by-year totals, and the $3.0M grand total.' "
                    "Never say 'here is the link', 'click to download', or paste any URL."
                ),
            }

        except Exception as e:
            logger.error(f"[s3_upload_tool] upload failed: {e}", exc_info=True)
            return {
                "success": False,
                "url": None,
                "filename": filename,
                "error": f"Upload failed: {e}",
            }

    return s3_upload_tool


# Default tool with no side-copy hook — importable exactly as before.
s3_upload_tool = build_s3_upload_tool()
