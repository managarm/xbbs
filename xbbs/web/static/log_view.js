// Supplemental JavaScript for the log viewing page.
// Copyright (C) 2025  Arsen Arsenović <arsen@managarm.org>

// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.

// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.

// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.

const logFrame = document.getElementById("raw-log-frame");
const rawLog = logFrame.dataset.logUrl;
logFrame.outerHTML = '<div id="raw-log-frame" class="grow-1"></div>';

const term = new Terminal({
    // We don't have a line discipline to translate \n to \r\n for us.
    convertEol: true,
    // Should be unlimited, but AFAICT it can't be :(
    scrollback: 999999,
    disableStdin: true,
});
const fitAddon = new FitAddon.FitAddon();
term.loadAddon(fitAddon);
const termDiv = document.getElementById("raw-log-frame");
termDiv.style.minHeight = '300px';
term.open(termDiv);
fitAddon.fit();

const termResizeObserver = new ResizeObserver(() => {
    fitAddon.fit()
});
termResizeObserver.observe(termDiv);

async function downloadLogs() {
    /* Number of bytes we already downloaded.  */
    let readAmount = 0;
    let iterationCount = 0;
    let hasMore = true;

    /* Update and download loop.  */
    while (hasMore) {
        iterationCount++;
        /* Download a chunk of data.  */
        const response = await fetch(rawLog, {
            headers: {
                Range: `bytes=${readAmount}-`
            }
        });
        const moreLogs = response.headers.get("X-Xbbs-More-Logs");
        if (moreLogs === "no") {
            if (iterationCount != 1) {
                /* Indicates that a state change occurred.  */
                location.reload();
            }

            /* Stop the loop after this iteration.  */
            hasMore = false;
        }
        if (response.status === 416) {
            /* Waiting for more logs  */
            await new Promise(res => setTimeout(res, 5000));
            continue;
        } else if (response.status !== 206) {
            /* Whoops.  TODO(arsen): figure out how to best report this error.  */
            return;
        }
        const reader = response.body.getReader();
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            readAmount += value.byteLength;
            term.write(value);
        }

        /* Chill out a bit.  If we get logs we are likely to soon see more, so wait for less time
           in this case.  */
        await new Promise(res => setTimeout(res, 1000));
    }
}

downloadLogs();
