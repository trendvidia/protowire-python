// SPDX-License-Identifier: MIT
// Copyright (c) 2026 TrendVidia, LLC.
// Python <-> protowire-cpp bridge.
//
// Boundary: Python passes a serialized FileDescriptorSet plus a fully-qualified
// message name. The C++ side builds a DescriptorPool, resolves the message,
// and creates a DynamicMessage on demand. Inputs and outputs cross the FFI
// as bytes (proto binary) or text — Message objects never escape C++.

#include <nanobind/nanobind.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/string_view.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>

#include <google/protobuf/descriptor.h>
#include <google/protobuf/descriptor.pb.h>
#include <google/protobuf/dynamic_message.h>
#include <google/protobuf/message.h>

#include "protowire/pxf.h"
#include "protowire/sbe.h"

namespace nb = nanobind;
using namespace nb::literals;
namespace pbuf = google::protobuf;

namespace {

// Build a fresh descriptor pool from a serialized FileDescriptorSet.
// The pool owns its FileDescriptors and outlives the call via shared_ptr.
struct SchemaBundle {
  std::shared_ptr<pbuf::DescriptorPool> pool;
  std::shared_ptr<pbuf::DynamicMessageFactory> factory;
  std::vector<const pbuf::FileDescriptor*> files;
};

SchemaBundle BuildSchema(std::string_view fds_bytes) {
  pbuf::FileDescriptorSet fds;
  if (!fds.ParseFromArray(fds_bytes.data(),
                          static_cast<int>(fds_bytes.size()))) {
    throw nb::value_error("invalid FileDescriptorSet bytes");
  }
  SchemaBundle b;
  b.pool = std::make_shared<pbuf::DescriptorPool>();
  for (const auto& fp : fds.file()) {
    const pbuf::FileDescriptor* fd = b.pool->BuildFile(fp);
    if (fd == nullptr) {
      std::string m = "FileDescriptorSet build failed for " + fp.name();
      throw nb::value_error(m.c_str());
    }
    b.files.push_back(fd);
  }
  b.factory = std::make_shared<pbuf::DynamicMessageFactory>(b.pool.get());
  return b;
}

const pbuf::Descriptor* FindDescriptor(const SchemaBundle& s,
                                       const std::string& full_name) {
  const auto* d = s.pool->FindMessageTypeByName(full_name);
  if (!d) {
    std::string m = "message type not found: " + full_name;
    throw nb::value_error(m.c_str());
  }
  return d;
}

// --- pxf bindings ---------------------------------------------------------

// CellToPyTuple converts a single AST cell value (or std::nullopt for an
// absent cell) into the FFI shape consumed by pxf.py — `None` for absent,
// `(kind, value)` otherwise. Used by PxfUnmarshalFull for @table rows.
//
// kind values mirror the AST variant tags:
//   "null"      → nb::none()
//   "string"    → str  (already-unescaped UTF-8)
//   "int"       → str  (raw integer text — Python wrapper decides parse)
//   "float"     → str  (raw float text)
//   "bool"      → bool
//   "bytes"     → bytes
//   "ident"     → str
//   "timestamp" → str  (raw RFC3339)
//   "duration"  → str  (raw duration)
nb::object CellToPyTuple(const std::optional<protowire::pxf::ValuePtr>& cell) {
  if (!cell.has_value()) return nb::none();
  using namespace protowire::pxf;
  return std::visit(
      [](const auto& p) -> nb::object {
        using T = std::decay_t<decltype(*p)>;
        if constexpr (std::is_same_v<T, NullVal>) {
          return nb::make_tuple(std::string("null"), nb::none());
        } else if constexpr (std::is_same_v<T, StringVal>) {
          return nb::make_tuple(std::string("string"), p->value);
        } else if constexpr (std::is_same_v<T, IntVal>) {
          return nb::make_tuple(std::string("int"), p->raw);
        } else if constexpr (std::is_same_v<T, FloatVal>) {
          return nb::make_tuple(std::string("float"), p->raw);
        } else if constexpr (std::is_same_v<T, BoolVal>) {
          return nb::make_tuple(std::string("bool"), p->value);
        } else if constexpr (std::is_same_v<T, BytesVal>) {
          return nb::make_tuple(
              std::string("bytes"),
              nb::bytes(reinterpret_cast<const char*>(p->value.data()), p->value.size()));
        } else if constexpr (std::is_same_v<T, IdentVal>) {
          return nb::make_tuple(std::string("ident"), p->name);
        } else if constexpr (std::is_same_v<T, TimestampVal>) {
          return nb::make_tuple(std::string("timestamp"), p->raw);
        } else if constexpr (std::is_same_v<T, DurationVal>) {
          return nb::make_tuple(std::string("duration"), p->raw);
        } else {
          // List / Block are rejected at @table cell-parse time, so this
          // branch is unreachable for cells. Surface as a clean error.
          return nb::make_tuple(std::string("unknown"), nb::none());
        }
      },
      *cell);
}

// PXF text -> binary proto bytes.
nb::bytes PxfUnmarshal(nb::bytes text, nb::bytes fds_bytes,
                       const std::string& full_name, bool discard_unknown,
                       bool skip_validate) {
  auto schema = BuildSchema(std::string_view(fds_bytes.c_str(), fds_bytes.size()));
  const auto* desc = FindDescriptor(schema, full_name);
  std::unique_ptr<pbuf::Message> msg(
      schema.factory->GetPrototype(desc)->New());

  protowire::pxf::UnmarshalOptions opts;
  opts.discard_unknown = discard_unknown;
  opts.skip_validate = skip_validate;
  auto st = protowire::pxf::Unmarshal(
      std::string_view(text.c_str(), text.size()), msg.get(), opts);
  if (!st.ok()) {
    throw nb::value_error(("pxf.unmarshal: " + st.ToString()).c_str());
  }
  std::string out;
  if (!msg->SerializeToString(&out)) {
    throw nb::value_error("pxf.unmarshal: proto serialization failed");
  }
  return nb::bytes(out.data(), out.size());
}

// Directive FFI shape: (name, prefixes, type, body, has_body, line, column).
using PyDirective = std::tuple<std::string, std::vector<std::string>, std::string,
                               nb::bytes, bool, int, int>;
// TableDirective FFI shape: (type, columns, rows) where rows is a list of
// lists of cells (each cell None or (kind, value); see CellToPyTuple).
using PyTableDirective = std::tuple<std::string, std::vector<std::string>,
                                    std::vector<std::vector<nb::object>>>;

// PXF text -> (binary proto bytes, set_paths, null_paths, directives, tables).
std::tuple<nb::bytes, std::vector<std::string>, std::vector<std::string>,
           std::vector<PyDirective>, std::vector<PyTableDirective>>
PxfUnmarshalFull(nb::bytes text, nb::bytes fds_bytes,
                 const std::string& full_name, bool discard_unknown,
                 bool skip_validate) {
  auto schema = BuildSchema(std::string_view(fds_bytes.c_str(), fds_bytes.size()));
  const auto* desc = FindDescriptor(schema, full_name);
  std::unique_ptr<pbuf::Message> msg(
      schema.factory->GetPrototype(desc)->New());

  protowire::pxf::UnmarshalOptions opts;
  opts.discard_unknown = discard_unknown;
  opts.skip_validate = skip_validate;
  auto r = protowire::pxf::UnmarshalFull(
      std::string_view(text.c_str(), text.size()), msg.get(), opts);
  if (!r.ok()) {
    throw nb::value_error(("pxf.unmarshal_full: " + r.status().ToString()).c_str());
  }
  std::string out;
  if (!msg->SerializeToString(&out)) {
    throw nb::value_error("pxf.unmarshal_full: proto serialization failed");
  }
  // Marshal directives.
  std::vector<PyDirective> py_dirs;
  py_dirs.reserve(r->Directives().size());
  for (const auto& d : r->Directives()) {
    py_dirs.emplace_back(
        d.name, d.prefixes, d.type,
        nb::bytes(d.body.data(), d.body.size()),
        d.has_body, d.pos.line, d.pos.column);
  }
  // Marshal tables.
  std::vector<PyTableDirective> py_tables;
  py_tables.reserve(r->Tables().size());
  for (const auto& t : r->Tables()) {
    std::vector<std::vector<nb::object>> py_rows;
    py_rows.reserve(t.rows.size());
    for (const auto& row : t.rows) {
      std::vector<nb::object> py_cells;
      py_cells.reserve(row.cells.size());
      for (const auto& cell : row.cells) py_cells.push_back(CellToPyTuple(cell));
      py_rows.push_back(std::move(py_cells));
    }
    py_tables.emplace_back(t.type, t.columns, std::move(py_rows));
  }
  return {nb::bytes(out.data(), out.size()),
          r->SetFields(),
          r->NullFields(),
          std::move(py_dirs),
          std::move(py_tables)};
}

// PXF schema reserved-name check (draft §3.13). Returns a list of
// (kind, element, name, file) tuples. Empty list ⇒ conformant schema.
// kind values: "field" / "oneof" / "enum_value".
std::vector<std::tuple<std::string, std::string, std::string, std::string>>
PxfValidateDescriptor(nb::bytes fds_bytes, const std::string& full_name) {
  auto schema = BuildSchema(std::string_view(fds_bytes.c_str(), fds_bytes.size()));
  const auto* desc = FindDescriptor(schema, full_name);
  auto vs = protowire::pxf::ValidateDescriptor(desc);
  std::vector<std::tuple<std::string, std::string, std::string, std::string>> out;
  out.reserve(vs.size());
  for (const auto& v : vs) {
    std::string kind;
    switch (v.kind) {
      case protowire::pxf::ViolationKind::kField:     kind = "field";      break;
      case protowire::pxf::ViolationKind::kOneof:     kind = "oneof";      break;
      case protowire::pxf::ViolationKind::kEnumValue: kind = "enum_value"; break;
    }
    out.emplace_back(std::move(kind), v.element, v.name, v.file);
  }
  return out;
}

// --- PyTableReader: streaming @table consumption -------------------------
//
// Wraps protowire::pxf::TableReader. The reader takes a std::istream*; we
// hold the istringstream alongside the reader so its lifetime is bound to
// the Python object. Input is provided as bytes (PR-2 scope); a file-like
// streambuf bridge is a possible follow-up.
class PyTableReader {
 public:
  static std::unique_ptr<PyTableReader> FromBytes(nb::bytes data) {
    auto out = std::unique_ptr<PyTableReader>(new PyTableReader());
    out->stream_ = std::make_unique<std::istringstream>(
        std::string(data.c_str(), data.size()));
    auto tr = protowire::pxf::TableReader::Create(out->stream_.get());
    if (!tr.ok()) {
      throw nb::value_error(("pxf.TableReader: " + tr.status().ToString()).c_str());
    }
    out->reader_ = std::move(*tr);
    // Marshal the side-channel directives once at construction; they're
    // fixed for the reader's lifetime.
    for (const auto& d : out->reader_->Directives()) {
      out->directives_.emplace_back(
          d.name, d.prefixes, d.type,
          nb::bytes(d.body.data(), d.body.size()),
          d.has_body, d.pos.line, d.pos.column);
    }
    return out;
  }

  const std::string& Type() const { return reader_->Type(); }
  const std::vector<std::string>& Columns() const { return reader_->Columns(); }
  const std::vector<PyDirective>& Directives() const { return directives_; }
  bool Done() const { return reader_->Done(); }

  // Returns the next row as a Python list of cells, or None at EOF.
  // Raises ValueError on parse error.
  nb::object NextOrNone() {
    if (reader_->Done()) return nb::none();
    protowire::pxf::TableRow row;
    auto s = reader_->Next(&row);
    if (!s.ok()) {
      throw nb::value_error(("pxf.TableReader.next: " + s.ToString()).c_str());
    }
    if (reader_->Done()) return nb::none();
    return RowToList(row);
  }

  // Iterator protocol: __next__ raises StopIteration at EOF.
  nb::object Next() {
    if (reader_->Done()) throw nb::stop_iteration();
    protowire::pxf::TableRow row;
    auto s = reader_->Next(&row);
    if (!s.ok()) {
      throw nb::value_error(("pxf.TableReader.next: " + s.ToString()).c_str());
    }
    if (reader_->Done()) throw nb::stop_iteration();
    return RowToList(row);
  }

  // Drains the remaining buffered + underlying bytes. Only meaningful
  // after Done(); the Python wrapper exposes this as a method that
  // returns bytes so callers can chain a second TableReader on
  // multi-@table documents.
  nb::bytes Tail() {
    auto t = reader_->Tail();
    std::ostringstream buf;
    buf << t->rdbuf();
    std::string s = buf.str();
    return nb::bytes(s.data(), s.size());
  }

 private:
  static nb::object RowToList(const protowire::pxf::TableRow& row) {
    std::vector<nb::object> cells;
    cells.reserve(row.cells.size());
    for (const auto& cell : row.cells) cells.push_back(CellToPyTuple(cell));
    return nb::cast(cells);
  }

  std::unique_ptr<std::istringstream> stream_;
  std::unique_ptr<protowire::pxf::TableReader> reader_;
  std::vector<PyDirective> directives_;
};

// Binary proto bytes -> PXF text.
std::string PxfMarshal(nb::bytes msg_bytes, nb::bytes fds_bytes,
                       const std::string& full_name) {
  auto schema = BuildSchema(std::string_view(fds_bytes.c_str(), fds_bytes.size()));
  const auto* desc = FindDescriptor(schema, full_name);
  std::unique_ptr<pbuf::Message> msg(
      schema.factory->GetPrototype(desc)->New());
  if (!msg->ParseFromArray(msg_bytes.c_str(),
                           static_cast<int>(msg_bytes.size()))) {
    throw nb::value_error("pxf.marshal: proto parse failed");
  }
  auto out = protowire::pxf::Marshal(*msg);
  if (!out.ok()) {
    throw nb::value_error(("pxf.marshal: " + out.status().ToString()).c_str());
  }
  return *out;
}

// --- sbe bindings ---------------------------------------------------------

// SbeCodec wraps protowire::sbe::Codec along with the descriptor pool.
class SbeCodec {
 public:
  static std::unique_ptr<SbeCodec> Create(
      nb::bytes fds_bytes, std::vector<std::string> file_names) {
    auto out = std::make_unique<SbeCodec>();
    out->schema_ = BuildSchema(std::string_view(fds_bytes.c_str(), fds_bytes.size()));

    // Codec::New requires every file have (sbe.schema_id) — the dependency
    // closure includes descriptor.proto and friends, so register only the
    // explicitly-listed files.
    std::vector<const pbuf::FileDescriptor*> selected;
    selected.reserve(file_names.size());
    for (const auto& name : file_names) {
      const auto* fd = out->schema_.pool->FindFileByName(name);
      if (!fd) {
        std::string m = "sbe.Codec: file not found in FDS: " + name;
        throw nb::value_error(m.c_str());
      }
      selected.push_back(fd);
    }
    if (selected.empty()) selected = out->schema_.files;

    auto codec = protowire::sbe::Codec::New(selected);
    if (!codec.ok()) {
      throw nb::value_error(
          ("sbe.Codec: " + codec.status().ToString()).c_str());
    }
    out->codec_ = std::make_unique<protowire::sbe::Codec>(
        std::move(codec).consume());
    return out;
  }

  // Binary proto bytes -> SBE bytes.
  nb::bytes Marshal(nb::bytes msg_bytes, const std::string& full_name) const {
    const auto* desc = FindDescriptor(schema_, full_name);
    std::unique_ptr<pbuf::Message> msg(
        schema_.factory->GetPrototype(desc)->New());
    if (!msg->ParseFromArray(msg_bytes.c_str(),
                             static_cast<int>(msg_bytes.size()))) {
      throw nb::value_error("sbe.Codec.marshal: proto parse failed");
    }
    auto out = codec_->Marshal(*msg);
    if (!out.ok()) {
      throw nb::value_error(
          ("sbe.Codec.marshal: " + out.status().ToString()).c_str());
    }
    return nb::bytes(reinterpret_cast<const char*>(out->data()), out->size());
  }

  // SBE bytes -> binary proto bytes.
  nb::bytes Unmarshal(nb::bytes data, const std::string& full_name) const {
    const auto* desc = FindDescriptor(schema_, full_name);
    std::unique_ptr<pbuf::Message> msg(
        schema_.factory->GetPrototype(desc)->New());
    auto st = codec_->Unmarshal(
        std::span<const uint8_t>(
            reinterpret_cast<const uint8_t*>(data.c_str()), data.size()),
        msg.get());
    if (!st.ok()) {
      throw nb::value_error(
          ("sbe.Codec.unmarshal: " + st.ToString()).c_str());
    }
    std::string out;
    if (!msg->SerializeToString(&out)) {
      throw nb::value_error("sbe.Codec.unmarshal: proto serialization failed");
    }
    return nb::bytes(out.data(), out.size());
  }

  // NewView constructs a top-level View over the given SBE-encoded buffer.
  // The returned PyView keeps the codec and a heap copy of the data alive
  // so that the C++ View's spans remain valid as long as Python holds the
  // PyView.
  class PyView; class PyGroupView;
  PyView NewView(nb::bytes data) const;

  const protowire::sbe::Codec& native() const { return *codec_; }

 private:
  SchemaBundle schema_;
  std::unique_ptr<protowire::sbe::Codec> codec_;
};

// PyView wraps protowire::sbe::View and tracks ownership of the underlying
// data buffer. Sub-views (composite + group entries) share the same buffer
// via shared_ptr so navigation is allocation-free at the data level.
class SbeCodec::PyView {
 public:
  PyView(std::shared_ptr<std::vector<uint8_t>> data,
         protowire::sbe::View view)
      : data_(std::move(data)), view_(view) {}

  int64_t Int(const std::string& name) const { return view_.Int(name); }
  uint64_t Uint(const std::string& name) const { return view_.Uint(name); }
  double Float(const std::string& name) const { return view_.Float(name); }
  bool Bool(const std::string& name) const { return view_.Bool(name); }
  std::string String(const std::string& name) const {
    return std::string(view_.String(name));
  }
  nb::bytes Bytes(const std::string& name) const {
    auto raw = view_.Bytes(name);
    return nb::bytes(reinterpret_cast<const char*>(raw.data()), raw.size());
  }

  PyView Composite(const std::string& name) const {
    return PyView(data_, view_.Composite(name));
  }

  PyGroupView Group(const std::string& name) const;

 private:
  std::shared_ptr<std::vector<uint8_t>> data_;
  protowire::sbe::View view_;
};

class SbeCodec::PyGroupView {
 public:
  PyGroupView(std::shared_ptr<std::vector<uint8_t>> data,
              protowire::sbe::GroupView group)
      : data_(std::move(data)), group_(group) {}

  size_t Len() const { return group_.Len(); }

  PyView Entry(size_t i) const {
    if (i >= group_.Len()) {
      throw nb::index_error("GroupView.entry: index out of range");
    }
    return PyView(data_, group_.Entry(i));
  }

 private:
  std::shared_ptr<std::vector<uint8_t>> data_;
  protowire::sbe::GroupView group_;
};

inline SbeCodec::PyView SbeCodec::NewView(nb::bytes data) const {
  auto buf = std::make_shared<std::vector<uint8_t>>(
      reinterpret_cast<const uint8_t*>(data.c_str()),
      reinterpret_cast<const uint8_t*>(data.c_str()) + data.size());
  auto view = codec_->NewView(std::span<const uint8_t>(buf->data(), buf->size()));
  if (!view.ok()) {
    throw nb::value_error(("sbe.View: " + view.status().ToString()).c_str());
  }
  return PyView(std::move(buf), *view);
}

inline SbeCodec::PyGroupView SbeCodec::PyView::Group(const std::string& name) const {
  return PyGroupView(data_, view_.Group(name));
}

}  // namespace

NB_MODULE(_protowire, m) {
  m.doc() = "protowire native extension (nanobind shim around protowire-cpp)";

  m.def("pxf_unmarshal", &PxfUnmarshal, "text"_a, "fds"_a, "full_name"_a,
        "discard_unknown"_a = false, "skip_validate"_a = false);
  m.def("pxf_unmarshal_full", &PxfUnmarshalFull, "text"_a, "fds"_a,
        "full_name"_a, "discard_unknown"_a = false, "skip_validate"_a = false);
  m.def("pxf_marshal", &PxfMarshal, "msg_bytes"_a, "fds"_a, "full_name"_a);
  m.def("pxf_validate_descriptor", &PxfValidateDescriptor, "fds"_a, "full_name"_a);

  nb::class_<PyTableReader>(m, "PxfTableReader")
      .def_static("from_bytes", &PyTableReader::FromBytes, "data"_a)
      .def_prop_ro("type", &PyTableReader::Type)
      .def_prop_ro("columns", &PyTableReader::Columns)
      .def_prop_ro("directives", &PyTableReader::Directives)
      .def_prop_ro("done", &PyTableReader::Done)
      .def("next_or_none", &PyTableReader::NextOrNone)
      .def("tail", &PyTableReader::Tail)
      .def("__iter__", [](PyTableReader& self) -> PyTableReader& { return self; })
      .def("__next__", &PyTableReader::Next);

  nb::class_<SbeCodec>(m, "SbeCodec")
      .def_static("create", &SbeCodec::Create, "fds"_a, "file_names"_a)
      .def("marshal", &SbeCodec::Marshal, "msg_bytes"_a, "full_name"_a)
      .def("unmarshal", &SbeCodec::Unmarshal, "data"_a, "full_name"_a)
      .def("new_view", &SbeCodec::NewView, "data"_a);

  nb::class_<SbeCodec::PyView>(m, "View")
      .def("int", &SbeCodec::PyView::Int, "name"_a)
      .def("uint", &SbeCodec::PyView::Uint, "name"_a)
      .def("float", &SbeCodec::PyView::Float, "name"_a)
      .def("bool", &SbeCodec::PyView::Bool, "name"_a)
      .def("string", &SbeCodec::PyView::String, "name"_a)
      .def("bytes", &SbeCodec::PyView::Bytes, "name"_a)
      .def("composite", &SbeCodec::PyView::Composite, "name"_a)
      .def("group", &SbeCodec::PyView::Group, "name"_a);

  nb::class_<SbeCodec::PyGroupView>(m, "GroupView")
      .def("len", &SbeCodec::PyGroupView::Len)
      .def("__len__", &SbeCodec::PyGroupView::Len)
      .def("entry", &SbeCodec::PyGroupView::Entry, "i"_a);
}
