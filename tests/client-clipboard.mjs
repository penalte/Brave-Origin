// Exercise the patched client module without a browser or clipboard permission.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(process.argv[2], 'utf8');
const { createClipboardGestures } = await import(
    `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
);
const listeners = new Map();
globalThis.window = {
    addEventListener: (name, handler) => listeners.set(name, handler),
    removeEventListener: (name) => listeners.delete(name),
};
globalThis.document = { activeElement: { id: 'overlayInput', tagName: 'INPUT' } };
const tick = () => new Promise((resolve) => setImmediate(resolve));

function setup(overrides = {}) {
    const calls = [];
    const gestures = createClipboardGestures({
        isChromium: true,
        clipboardSync: {},
        canSync: () => true,
        canRead: () => true,
        canWrite: () => true,
        binaryEnabled: () => true,
        getSendInFlight: () => null,
        sendClipboardData: async (data, mime) => calls.push(['transfer', data, mime]),
        pasteRemote: () => calls.push(['paste']),
        ...overrides,
    });
    gestures.wire();
    return { calls, gestures };
}

function paste(text, items = []) {
    const event = {
        clipboardData: { getData: () => text, items },
        prevented: false,
        preventDefault() { this.prevented = true; },
    };
    listeners.get('paste')(event);
    return event;
}

// A menu paste must wait for the actual transfer before injecting Ctrl+V.
let release;
const order = [];
let run = setup({
    sendClipboardData: (data, mime) => {
        order.push(['transfer', data, mime]);
        return new Promise((resolve) => { release = resolve; });
    },
    pasteRemote: () => order.push(['paste']),
});
const text = 'Clipboard café 中文 🎉\nsecond line\n';
assert.equal(paste(text).prevented, true);
await tick();
assert.deepEqual(order, [['transfer', text, 'text/plain']]);
release();
await tick();
assert.deepEqual(order.at(-1), ['paste']);
run.gestures.unwire();

// Dashboard form fields and disabled clipboard access remain local.
run = setup();
document.activeElement = { id: 'dashboardClipboardTextarea', tagName: 'TEXTAREA' };
assert.equal(paste(text).prevented, false);
await tick();
assert.deepEqual(run.calls, []);
run.gestures.unwire();
document.activeElement = { id: 'overlayInput', tagName: 'INPUT' };
run = setup({ canRead: () => false });
assert.equal(paste(text).prevented, false);
await tick();
assert.deepEqual(run.calls, []);
run.gestures.unwire();

// Image transfer takes precedence over a text alternative.
run = setup();
const png = new Uint8Array([137, 80, 78, 71]).buffer;
paste('image alternative', [{
    kind: 'file', type: 'image/png',
    getAsFile: () => ({ arrayBuffer: async () => png }),
}]);
await tick();
assert.deepEqual(run.calls, [['transfer', png, 'image/png'], ['paste']]);
run.gestures.unwire();

// Failed transfers must never paste the previous clipboard contents.
run = setup({ sendClipboardData: async () => { throw new Error('test transfer failure'); } });
paste(text);
await tick();
assert.deepEqual(run.calls, []);
run.gestures.unwire();

// Other engines already forward the physical chord: do not paste twice.
run = setup({ isChromium: false });
assert.equal(paste(text).prevented, false);
await tick();
assert.deepEqual(run.calls, [['transfer', text, 'text/plain']]);
run.gestures.unwire();
assert.equal(listeners.size, 0);
console.log('Client paste passed: Unicode, transfer ordering, images, permissions, local fields, failure handling.');
