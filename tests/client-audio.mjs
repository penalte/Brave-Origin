import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

// The pinned core keeps its pure RED parser above the browser entry point.
const source = await readFile(process.argv[2], 'utf8');
const parser = source.slice(source.indexOf('let lastAudioTs = null;'),
    source.indexOf('export default function websockets()'));
const context = vm.createContext({ Uint8Array });
vm.runInContext(parser, context);
const { extractOpusFrames, resetAudioTimestamp } = context;

function packet(timestamp) {
    const bytes = new Uint8Array(13);
    bytes[0] = 1;
    bytes[1] = 1;
    new DataView(bytes.buffer).setUint32(2, timestamp);
    // One redundant byte, ten timestamp units behind the primary byte.
    bytes[8] = 40;
    bytes[9] = 1;
    bytes[11] = 11;
    bytes[12] = 22;
    return bytes.buffer;
}

assert.equal(extractOpusFrames(packet(100000)).length, 1);
assert.equal(extractOpusFrames(packet(100000)).length, 0, 'Duplicate audio was replayed');
assert.equal(extractOpusFrames(packet(100020)).length, 2, 'Redundancy did not recover the missing frame');
// Restarting the capture resets its timestamp. Old state must not mute it.
resetAudioTimestamp();
const restarted = extractOpusFrames(packet(20));
assert.equal(restarted.length, 1, 'Audio stayed muted after restarting capture');
assert.equal(new Uint8Array(restarted[0])[0], 22);
assert.equal(extractOpusFrames(packet(40)).length, 2);
resetAudioTimestamp();
assert.equal(extractOpusFrames(packet(0xfffffff0)).length, 1);
assert.equal(extractOpusFrames(packet(20)).length, 2, 'Timestamp wrap lost audio');
console.log('Client audio passed: duplicate suppression, packet recovery, restart, timestamp wrap.');
