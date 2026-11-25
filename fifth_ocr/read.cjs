// Local-only OCR: pinned engine and model, no runtime downloads or cache writes.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const zlib = require('zlib');
const { createWorker } = require('tesseract.js');
const started = Date.now();
let stage = 'model_identity';
let regions = false;
let count = 0;
let completed = 0;
function diagnostic(status) {
  if (regions) process.stderr.write(JSON.stringify({ stage, status, regions: count,
    completed, elapsed_ms: Date.now() - started }) + '\n');
}

async function main() {
  const args = process.argv.slice(2);
  regions = args[0] === '--regions';
  const files = regions ? args.slice(1) : args;
  count = files.length;
  const verify = !regions && files.length === 1 && files[0] === '--verify';
  if (!verify && (files.length < 1 || files.length > (regions ? 16 : 2))) {
    stage = 'input_bound';
    throw Error('Unsupported page count');
  }
  const langPath = path.join(__dirname, 'node_modules/@tesseract.js-data/eng/4.0.0_best_int');
  const hash = crypto.createHash('sha256').update(zlib.gunzipSync(
    fs.readFileSync(path.join(langPath, 'eng.traineddata.gz')))).digest('hex');
  if (hash !== '5dc5d8d640a212c9d6184921ba103b186f50e0fed9ee716c53e6b312b400d747'
      || require('tesseract.js/package.json').version !== '7.0.0'
      || require('tesseract.js-core/package.json').version !== '7.0.0') {
    throw Error('OCR dependency identity mismatch');
  }
  diagnostic('ok');
  stage = 'worker_start';
  diagnostic('begin');
  const worker = await createWorker('eng', 1, { langPath, cacheMethod: 'none' });
  diagnostic('ok');
  try {
    stage = 'recognize';
    diagnostic('begin');
    const pages = [];
    if (!verify) for (const file of files) {
      pages.push((await worker.recognize(file)).data.text);
      completed += 1;
      diagnostic('ok');
    }
    diagnostic('ok');
    process.stdout.write(JSON.stringify({ pages, engine: '7.0.0', modelHash: hash }));
  } catch (error) {
    diagnostic('error');
    throw error;
  } finally {
    stage = 'worker_stop';
    await worker.terminate();
    diagnostic('ok');
  }
}
main().catch(() => { diagnostic('error'); process.stderr.write('OCR unavailable or failed validation'); process.exitCode = 1; });
