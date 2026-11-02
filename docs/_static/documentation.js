/* Progressive enhancement: repeat actual column headings on narrow table rows.
 * Leave complex spanned tables and the original semantic table markup intact.
 * Without JavaScript the wrapped, bounded table remains available.
 */
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".rst-content div.highlight").forEach((block, index) => {
    const pre = block.querySelector("pre");
    if (!pre) return;
    const tools = document.createElement("div");
    tools.className = "code-tools";
    const number = document.createElement("span");
    number.textContent = `Code ${index + 1}`;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy-code-button";
    button.setAttribute("aria-label", `Copy code block ${index + 1}`);
    button.title = "Copy code (without line numbers)";
    button.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><rect x="8" y="8" width="12" height="13" rx="1" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M16 8V3H3v13h5" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>';
    const status = document.createElement("span");
    status.className = "copy-code-status";
    status.setAttribute("role", "status");
    button.addEventListener("click", async () => {
      const source = pre.cloneNode(true);
      source.querySelectorAll(".linenos").forEach((line) => line.remove());
      const text = source.textContent;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
        } else {
          const textarea = document.createElement("textarea");
          textarea.value = text;
          textarea.className = "clipboard-fallback";
          document.body.appendChild(textarea);
          try {
            textarea.select();
            if (!document.execCommand("copy")) throw new Error("Copy unavailable");
          } finally {
            textarea.remove();
            button.focus();
          }
        }
        status.textContent = "Copied";
      } catch {
        status.textContent = "Copy unavailable — select the code manually";
      }
    });
    tools.append(number, status, button);
    block.insertBefore(tools, pre);
  });

  // RTD may add its responsive wrapper after DOMContentLoaded; select the
  // semantic tables themselves rather than depending on that wrapper's timing.
  document.querySelectorAll("table.docutils").forEach((table) => {
    const headings = [...table.querySelectorAll("thead tr")];
    if (headings.length !== 1) return;
    const labels = [...headings[0].cells].map((cell) => cell.textContent.trim());
    const rows = [...table.querySelectorAll("tbody tr")];
    if (!labels.length || labels.some((label) => !label) || !rows.length) return;
    if ([...table.querySelectorAll("th, td")].some((cell) => cell.colSpan !== 1 || cell.rowSpan !== 1)) return;
    if (rows.some((row) => row.cells.length !== labels.length)) return;
    rows.forEach((row) => {
      [...row.cells].forEach((cell, index) => {
        const label = document.createElement("span");
        label.className = "mobile-column-label";
        label.textContent = labels[index];
        label.setAttribute("aria-hidden", "true");
        const value = document.createElement("div");
        value.className = "mobile-cell-value";
        while (cell.firstChild) value.appendChild(cell.firstChild);
        cell.append(label, value);
      });
    });
    table.classList.add("responsive-cards");
  });
});
