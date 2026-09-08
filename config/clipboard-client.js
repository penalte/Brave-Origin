// Use KasmVNC's existing clipboard transport; serialize transfer before paste keys.
export function createClipboardHandler(getRfb, isRemote, onError) {
    let pending = Promise.resolve();
    let capturedKey = false;
    const available = () => {
        const rfb = getRfb();
        return rfb && rfb.clipboardUp && !rfb.viewOnly;
    };
    function enqueue(transfer) {
        const rfb = getRfb();
        pending = pending.then(async () => {
            if (rfb !== getRfb() || !available()) return;
            await transfer(rfb);
            // Release modifiers that the remote keyboard may already have seen.
            for (const [key, code] of [[65507,'ControlLeft'],[65508,'ControlRight'],
                [65505,'ShiftLeft'],[65506,'ShiftRight'],[65511,'MetaLeft'],[65512,'MetaRight']]) {
                rfb.sendKey(key, code, false);
            }
            rfb.sendKey(65507, 'ControlLeft', true);
            rfb.sendKey(118, 'KeyV', true);
            rfb.sendKey(118, 'KeyV', false);
            rfb.sendKey(65507, 'ControlLeft', false);
        }).catch(onError);
        return pending;
    }
    return {
        keydown(event) {
            if (available() && isRemote(event.target) && (event.ctrlKey || event.metaKey) &&
                !event.altKey && event.key.toLowerCase() === 'v') {
                // Let the browser generate paste; prevent KasmVNC forwarding V twice.
                capturedKey = true;
                event.stopImmediatePropagation();
            }
        },
        keyup(event) {
            if (capturedKey && event.key.toLowerCase() === 'v') {
                capturedKey = false;
                event.stopImmediatePropagation();
            }
        },
        paste(event) {
            if (!available() || !isRemote(event.target) || !event.clipboardData) return;
            const png = [...(event.clipboardData.items || [])]
                .find(item => item.kind === 'file' && item.type === 'image/png')?.getAsFile();
            const text = event.clipboardData.getData('text/plain');
            if (!png && !text) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            return enqueue(rfb => png
                ? rfb.clipboardPasteDataFrom([{types:['image/png'], getType:async () => png}])
                : rfb.clipboardPasteFrom(text));
        },
        sendText(text) {
            if (text && available()) return enqueue(rfb => rfb.clipboardPasteFrom(text));
            return pending;
        }
    };
}

export function installClipboard(UI) {
    const isRemote = target => target === document.body || target?.tagName === 'CANVAS' ||
        target?.id === 'noVNC_keyboardinput';
    const status = document.createElement('div');
    status.setAttribute('role', 'status');
    const handler = createClipboardHandler(() => UI.rfb, isRemote, () => {
        status.textContent = 'Paste failed. Reconnect and try again.';
    });
    for (const name of ['keydown', 'keyup', 'paste']) window.addEventListener(name, handler[name], true);
    const field = document.getElementById('noVNC_clipboard_text');
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Paste to session';
    button.addEventListener('click', () => {
        status.textContent = '';
        if (!UI.rfb?.clipboardUp || UI.rfb.viewOnly) {
            status.textContent = 'Enable Clipboard Up and turn off View Only to paste.';
            return;
        }
        handler.sendText(field.value);
    });
    field.insertAdjacentElement('afterend', button);
    button.insertAdjacentElement('afterend', status);
}
