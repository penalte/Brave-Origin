import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../config/clipboard-client.js', import.meta.url), 'utf8');
const { createClipboardHandler } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
let events = [];
let errors = [];
const rfb = {
    clipboardUp: true, viewOnly: false,
    clipboardPasteFrom(text) { events.push(['text', text]); },
    async clipboardPasteDataFrom(items) {
        await new Promise(resolve => setTimeout(resolve, 10));
        events.push(['image', await items[0].getType('image/png')]);
    },
    sendKey(key, code, down) { events.push(['key', key, down]); }
};
const handler = createClipboardHandler(() => rfb, target => target === 'remote', err => errors.push(err));
const event = (text, png, target = 'remote') => ({
    target, stopped: false, prevented: false,
    clipboardData: {getData: () => text, items: png ? [{kind:'file',type:'image/png',getAsFile:() => png}] : []},
    preventDefault() { this.prevented = true; },
    stopImmediatePropagation() { this.stopped = true; }
});
const text = 'café 日本語 🎉\nsecond line';
let paste = event(text);
await handler.paste(paste);
assert(paste.stopped && paste.prevented);
assert.deepEqual(events[0], ['text', text]);
assert.equal(events.filter(x => x[0] === 'key' && x[1] === 118 && x[2]).length, 1);
events = [];
const png = new Blob(['test image bytes'], {type:'image/png'});
await Promise.all([handler.paste(event('image fallback text', png)), handler.sendText('next paste')]);
assert.deepEqual(events[0], ['image', png]);
assert(events.findIndex(x => x[0] === 'text') > events.findIndex(x => x[0] === 'key'));
const length = events.length;
await handler.paste(event('local form', null, 'field'));
assert.equal(events.length, length);
rfb.clipboardUp = false;
await handler.paste(event('disabled'));
assert.equal(events.length, length);
rfb.clipboardUp = true;
rfb.clipboardPasteFrom = () => { throw Error('transfer failed'); };
await handler.paste(event('failed'));
assert.equal(events.length, length, 'Failed transfer must not paste stale contents');
assert.equal(errors.length, 1);
const key = {target:'remote',key:'v',ctrlKey:true,stopImmediatePropagation(){this.stopped=true;}};
handler.keydown(key);
assert(key.stopped && !key.prevented, 'Native paste default must remain enabled');
console.log('Native clipboard order, image transfer, form isolation, disabled access, and failure checks passed.');
