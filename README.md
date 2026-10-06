# luce-xml

A conforming, non-validating XML 1.0 (Fifth Edition) and Namespaces in XML 1.0 parser
for Luce/Base. It streams a document to a listener as events, for programs that build
their own model (the browser's XML document builder), and builds a small tree of nodes
for programs that want one (luce-svg). It depends on no other package: only the
compiler's built-in modules.

```luce
from luce_xml import xml

struct Printer: xml.Listener:
    var elements: usize = 0

    pub func event(event: xml.Event) -> !:
        match event:
            .element_start(element):
                self.elements += 1
                print(f"<{element.name.local_name}> in {element.name.namespace}")
            .text(text):
                print(f"text: {text}")
            _:
                return

    pub func resolve(request: const xml.EntityRequest*) -> const u8[]?:
        _ = request
        return none

var parser = xml.Parser()
defer parser.release()
var printer = Printer()
parser.parse(source, &printer) catch failure:
    if let found = parser.failure:
        print(f"{found.position.line}:{found.position.column}: {found.message}")
```

## Events and the listener

`Parser.parse(source, listener)` reads a whole document and sends a `Listener` its
`Event`s in document order:

| Event | When |
| --- | --- |
| `document_start(declaration)` | the XML declaration is read: `version`, `encoding`, `standalone`, and `source`, the document decoded to UTF-8 |
| `doctype(doctype)` | the DOCTYPE is read, both subsets: `name`, `public_id`, `system_id`, `internal_subset` as written, `notations` |
| `element_start(element)` | a start tag: `name`, `attributes` (ordered: as written, then the DTD's defaults), `empty` for `<x/>` |
| `element_end(name)` | an end tag, or right after an empty element's start |
| `text(text)` | character data, references resolved; adjacent text is one event |
| `cdata(text)` | a CDATA section's contents |
| `processing_instruction(target, data)` | a PI in the document (not those in the DTD) |
| `comment(text)` | a comment in the document (not those in the DTD) |
| `parse_error(failure)` | the parse stopped at a well-formedness error |
| `document_end` | the document ended well-formed |

Views in events last only until the listener returns. A `Name` carries the name as
written (`qualified`) and, with namespaces on, its `prefix`, `local_name` and resolved
`namespace` URI; namespace declarations are attributes in the `xmlns_namespace`, so a
builder that resolves namespaces its own way (as the DOM's "validate and extract" does)
has everything it needs. An `Attribute` has its `name`, its normalised `value`, and
`specified` (false for a default). A listener that fails stops the parse with its error.

`Listener.resolve(request)` is where the parser asks for what it never fetches itself:

- `document_type`: the external DTD subset of `<!DOCTYPE d SYSTEM "...">`;
- `parameter`, `general`: an external parameter entity, or an external general entity
  referenced in content (`public_id`, `system_id` as written, and `base`, the system
  identifier of the entity that declares it, to resolve a relative one against);
- `named`: a general entity the document does not declare, where XML allows that (the
  document has an external subset or parameter entities and is not standalone). The
  answer is taken as literal characters, never markup: this is how a browser gives
  XHTML documents HTML's named characters (`&nbsp;`, `&eacute;`) without reading the
  XHTML DTD.

Answering `none` skips the entity, as a non-validating processor may; a parameter
entity not read stops the processing (not the checking) of later ENTITY and ATTLIST
declarations, as §5.1 requires. The bytes answered are decoded like a document (byte
order mark, text declaration) and must live until `parse` returns. `xml.Checker` is a
listener that ignores everything and resolves nothing; `xml.check(source)` answers
whether a document is well-formed.

## Errors

A well-formedness or namespace error stops the parse: `parse` fails with `malformed`,
the listener gets `parse_error`, and `Parser.failure` holds a `ParseError` with the
`position` (line and column from 1, the column in characters, and the byte offset into
the original bytes — into the UTF-8 decoding for a UTF-16 or Latin-1 document), the
`entity` (the system identifier of the external entity the position is in, "" for the
document) and a `message` written for a person:

```text
3:1: the end tag </a> does not match the start tag <b>
1:24: '<' in the value of the attribute 'href' must be written &lt;
5:9: the entity 'e' ends inside the element <foo> it started
2:16: the prefix 'svg' of 'svg:rect' is not declared
```

`unsupported_encoding` reports an encoding the parser does not decode, naming it in
`ParseError.encoding`; `limit` reports a limit of `Options` reached. `xml.describe`
writes a failure as `entity:line:column: message`.

## Options

| Option | Default | |
| --- | --- | --- |
| `namespaces` | true | resolve prefixes and enforce Namespaces in XML 1.0 |
| `assume_utf8` | false | the caller decoded the document already (a browser does, from the transport's charset): ignore the declared encoding |
| `max_depth` | 5000 | the deepest elements may nest (and content models) |
| `max_expansion` | 16 MiB | the bytes all entity references together may expand to |
| `max_entity_depth` | 64 | the deepest entity references may nest |

## What is checked and done

- **Characters and encodings.** UTF-8 (with or without a byte order mark), UTF-16 in
  either byte order (with a byte order mark, or labelled UTF-16LE/BE), ISO-8859-1 and
  US-ASCII are decoded; the declaration must agree with the bytes. Every character must
  be an XML `Char`; line ends are normalised (CR LF and CR become LF) before parsing.
  Names follow the Fifth Edition's `NameStartChar`/`NameChar`.
- **The prolog.** The XML declaration (version 1.x, encoding name, standalone) only at
  the very start; comments and PIs (no `--` in comments, no `xml` targets), one DOCTYPE,
  one root element.
- **Elements and attributes.** Matching end tags, unique attribute names, quoted
  values without `<`, character and entity references, `]]>` never in text. Values are
  normalised per §3.3.3: references replaced, white space made spaces, and, for
  attributes the DTD declares with a type other than CDATA, spaces collapsed.
- **The DTD.** Every declaration is checked against its grammar: ELEMENT content models
  (mixed and children, no mixing of `|` and `,`), ATTLIST types and defaults, ENTITY
  (internal, external, unparsed), NOTATION, conditional sections (external subset
  only), parameter-entity references (between declarations; inside them only in the
  external subset), PIs and comments. Entities: the first declaration binds, the five
  predefined ones are built in, replacement texts are built as §4.5 says, references
  must be declared where XML requires it (WFC: Entity Declared, including standalone
  documents), must not recurse, must not name unparsed entities, external entities in
  attribute values, or text holding `<` in an attribute value. An entity's text must
  hold whole elements. Defaults from ATTLIST declarations are added to start tags.
- **Namespaces.** QNames (one colon at most), declared prefixes, `xml` and `xmlns`
  reserved and their namespaces likewise, no undeclaring of prefixes (`xmlns:p=""`),
  attributes unique by namespace and local name, no colons in entity names, PI
  targets or notation names.

Not done, as a non-validating parser need not: validity constraints (content models
are checked for syntax, not enforced), ID uniqueness, and the attribute types' value
syntax.

## The tree

```luce
var document = xml.Document()
defer document.release()
try document.load(source)
let root = document.root() else return
let width = document.attribute(root, "width")
let group = document.child_element(root, "g", "http://www.w3.org/2000/svg")
```

`Document.load` builds nodes (`NodeKind`: document, element, text, cdata, comment,
processing_instruction) held in one list and addressed by `u32` index, node 0 being the
document; each `Node` has its `name`, `value`, and `parent`, `first_child`,
`last_child`, `next_sibling`, `previous_sibling` links (0 for none). `attributes(index)`,
`attribute(index, local_name, namespace)`, `child_element`, `text_content(index, sink)`
query it; `declaration` and `doctype` keep the prolog. Every string is the document's
own copy. External entities are not read. `failure` explains a failed load.

## Conformance

`tools/xmlconf.py` runs the W3C XML Conformance Test Suite (xmlts20130923, from
https://www.w3.org/XML/Test/, unpacked into `../.donors/xmlconf`; it is never committed
here) through `tools/xmlconf.lucb`: every XML 1.0 and Namespaces 1.0 test of a
non-validating processor, by the Fifth Edition's rules where tests are edition-specific
(the XML 1.1 and Namespaces 1.1 tests are left out). Valid and invalid documents must be
accepted (invalid ones are well-formed), not-wf documents rejected, and where the suite
gives a document's canonical output, the parser's events written in that form must match
it byte for byte. Tests marked NAMESPACE="no" run with namespaces off; external entities
are read from the suite's files through `Listener.resolve`; documents in encodings the
parser leaves to its caller (Shift_JIS, EUC-JP, ISO-2022-JP, Big5…) are transcoded by
the runner and parsed with `assume_utf8`, as a browser hands them over.

2026-10-04, with luce-base e7baf89 and luce-base main 0.35.4 alike:

| Collection | valid | invalid | not-wf | canonical output | error (optional) |
| --- | --- | --- | --- | --- | --- |
| xmltest (James Clark) | 163/163 | 4/4 | 195/195 | 164/164 | 0/1 |
| sun | 28/28 | 74/74 | 56/56 | 27/27 | 0/1 |
| ibm | 149/149 | 40/40 | 423/423 | 177/180 | 1/9 |
| oasis | 46/46 | 54/54 | 247/247 | – | 0/1 |
| japanese | 6/6 | – | – | – | 0/6 |
| eduni (Edinburgh) | 336/336 | 57/57 | 96/96 | 8/8 | 3/9 |
| **all** | **728/728** | **229/229** | **1017/1017** | **376/379** | 4/27 |

The three outputs that differ (ibm28v02, ibm29v01, ibm29v02) expect a processing
instruction from the internal subset to appear as document content; the parser treats
PIs in the DTD as part of the DTD, as the DOM does. "Error" tests are errors a
processor may report or not (validity errors, encodings); they are only counted.

With `--no-external` (every external entity unread, as in a browser) the same 728
valid and 229 invalid documents are accepted and all 951 not-wf documents whose error
is in the document itself are rejected.

## Tests

`./test.sh` builds and runs the unit tests (`tests/xml/`, test fragments the module's
`TESTS` lists: structure, text and references, the DTD, entities, namespaces, encodings,
limits, the tree) in native and C modes, checks the sources with `-W` and their
formatting, and builds the tools, with the compiler `LUCE_BASE` names and each one
`LUCE_BASE_EXTRA` lists (colon-separated), so one run can cover a released toolchain
and luce-base main.

`tools/fuzz.py` checks robustness: it mutates the suite's documents and the entities
beside them (bytes flipped, inserted, deleted, repeated, spliced from other documents,
and XML's own tokens dropped in: `<`, `&`, `]]>`, `<!ENTITY e '&e;'>`, `%p;`, byte
order marks, broken UTF-8, deep nesting…) and parses them in batches, narrowing any
batch that traps or hangs to the case responsible. Before that, a stress phase parses
large and adversarial documents (16 MB of markup, 100,000 attributes on a tag, 20,000
namespace declarations, 100,000 entities or notations, a billion laughs, a quadratic
blow-up, a million references in one attribute, 5 MB of white space in a tag), each of
which must finish within two seconds. 2026-10-04: 200,000 mutated cases with no trap
or hang; every stress document in at most 0.31 s, the 16.5 MB one included; peak memory
of a parsing process 57 MB (the tool holds that whole 16.5 MB document).

## License

MIT OR Apache-2.0.
