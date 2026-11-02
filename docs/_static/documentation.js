/* Progressive enhancement: repeat actual column headings on narrow table rows.
 * Leave complex spanned tables and the original semantic table markup intact.
 * Without JavaScript the wrapped, bounded table remains available.
 */
document.addEventListener("DOMContentLoaded", () => {
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
