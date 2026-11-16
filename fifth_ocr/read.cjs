// Local-only OCR: pinned engine and model, no runtime downloads or cache writes.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const zlib = require('zlib');
const { createWorker } = require('tesseract.js');

async function main() {
  const langPath = path.join(__dirname, 'node_modules/@tesseract.js-data/eng/4.0.0_best_int');
  const hash = crypto.createHash('sha256').update(zlib.gunzipSync(
    fs.readFileSync(path.join(langPath, 'eng.traineddata.gz')))).digest('hex');
  if (hash !== '5dc5d8d640a212c9d6184921ba103b186f50e0fed9ee716c53e6b312b400d747'
      || require('tesseract.js/package.json').version !== '7.0.0'
      || require('tesseract.js-core/package.json').version !== '7.0.0') {
    throw Error('OCR dependency identity mismatch');
  }
  const files = process.argv.slice(2);
  const verify = files.length === 1 && files[0] === '--verify';
  if (!verify && (files.length < 1 || files.length > 2)) throw Error('Unsupported page count');
  const worker = await createWorker('eng', 1, { langPath, cacheMethod: 'none' });
  try {
    const pages = [];
    if (!verify) for (const file of files) pages.push((await worker.recognize(file)).data.text);
    process.stdout.write(JSON.stringify({ pages, engine: '7.0.0', modelHash: hash }));
  } finally {
    await worker.terminate();
  }
}
main().catch(() => { process.stderr.write('OCR unavailable or failed validation'); process.exitCode = 1; });
