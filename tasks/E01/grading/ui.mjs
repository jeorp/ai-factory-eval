import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const {buildRequest} = await import(pathToFileURL(process.argv[2]).href);
for (const language of ['en','ja']) {
 const request = buildRequest('hello', language);
 assert.equal(request.language, language);
 assert.equal(JSON.parse(JSON.stringify(request)).language, language);
 assert.equal(request.text, 'hello');
}
console.log('TestUI passed');
