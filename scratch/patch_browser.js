const fs = require('fs');
let code = fs.readFileSync('tests/browser.py', 'utf8');
code = code.replace(
    'await page.goto(url)',
    'page.on("console", lambda msg: print(f"BROWSER CONSOLE: {msg.type} {msg.text}"))\n        await page.goto(url)'
);
fs.writeFileSync('tests/browser.py', code);
