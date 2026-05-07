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
#include <span>
#include <string>
#include <string_view>
#include <utility>
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

// PXF text -> binary proto bytes.
nb::bytes PxfUnmarshal(nb::bytes text, nb::bytes fds_bytes,
                       const std::string& full_name, bool discard_unknown) {
  auto schema = BuildSchema(std::string_view(fds_bytes.c_str(), fds_bytes.size()));
  const auto* desc = FindDescriptor(schema, full_name);
  std::unique_ptr<pbuf::Message> msg(
      schema.factory->GetPrototype(desc)->New());

  protowire::pxf::UnmarshalOptions opts;
  opts.discard_unknown = discard_unknown;
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

// PXF text -> (binary proto bytes, set_paths, null_paths).
std::tuple<nb::bytes, std::vector<std::string>, std::vector<std::string>>
PxfUnmarshalFull(nb::bytes text, nb::bytes fds_bytes,
                 const std::string& full_name, bool discard_unknown) {
  auto schema = BuildSchema(std::string_view(fds_bytes.c_str(), fds_bytes.size()));
  const auto* desc = FindDescriptor(schema, full_name);
  std::unique_ptr<pbuf::Message> msg(
      schema.factory->GetPrototype(desc)->New());

  protowire::pxf::UnmarshalOptions opts;
  opts.discard_unknown = discard_unknown;
  auto r = protowire::pxf::UnmarshalFull(
      std::string_view(text.c_str(), text.size()), msg.get(), opts);
  if (!r.ok()) {
    throw nb::value_error(("pxf.unmarshal_full: " + r.status().ToString()).c_str());
  }
  std::string out;
  if (!msg->SerializeToString(&out)) {
    throw nb::value_error("pxf.unmarshal_full: proto serialization failed");
  }
  return {nb::bytes(out.data(), out.size()),
          r->SetFields(),
          r->NullFields()};
}

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
        "discard_unknown"_a = false);
  m.def("pxf_unmarshal_full", &PxfUnmarshalFull, "text"_a, "fds"_a,
        "full_name"_a, "discard_unknown"_a = false);
  m.def("pxf_marshal", &PxfMarshal, "msg_bytes"_a, "fds"_a, "full_name"_a);

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
