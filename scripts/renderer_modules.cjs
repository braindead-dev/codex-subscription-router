"use strict";

// Parse module boundaries; minified identifiers are local to these closures.
const fs = require("node:fs");
const path = require("node:path");
const acorn = require("acorn");

function modules(source, file) {
  const ast = acorn.parse(source, { ecmaVersion: "latest", sourceType: "module" });
  const result = [];
  for (const statement of ast.body) {
    if (statement.type !== "ExportNamedDeclaration") continue;
    for (const declaration of statement.declaration?.declarations ?? []) {
      if (declaration.id.name !== "__webpack_modules__") continue;
      if (declaration.init?.type !== "ObjectExpression") throw Error(`Invalid module table in ${file}`);
      for (const property of declaration.init.properties) {
        if (property.type !== "Property" || property.computed || property.value.type !== "FunctionExpression") {
          throw Error(`Unsupported module definition in ${file}`);
        }
        const id = property.key.name ?? property.key.value;
        if (typeof id !== "string" && typeof id !== "number") throw Error(`Invalid module ID in ${file}`);
        if (property.value.params.some(param => param.type !== "Identifier")) throw Error(`Invalid module arguments in ${file}`);
        result.push({
          id: String(id), file, start: property.start, end: property.end,
          params: property.value.params.map(param => param.name),
          source: source.slice(property.start, property.end),
        });
      }
    }
  }
  return result;
}

function inventory(root) {
  const result = [];
  const seen = new Set();
  for (const file of fs.readdirSync(root, { recursive: true }).filter(file => file.endsWith(".js")).sort()) {
    const source = fs.readFileSync(path.join(root, file), "utf8");
    if (!source.includes("__webpack_modules__")) continue;
    for (const module of modules(source, file)) {
      // Repeated IDs in distinct chunks can be legitimate. Retain every
      // occurrence so the caller must resolve ambiguity instead of overwriting.
      const key = `${module.file}:${module.start}`;
      if (seen.has(key)) throw Error(`Duplicate module location ${key}`);
      seen.add(key);
      result.push(module);
    }
  }
  return result;
}

module.exports = { modules, inventory };
if (require.main === module) {
  if (process.argv.length !== 3) throw Error("Usage: node scripts/renderer_modules.cjs <assets-directory>");
  process.stdout.write(JSON.stringify(inventory(process.argv[2])));
}
