#include "imagedata.h"

#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <string>

namespace {

void WriteJsonString(const std::string &value) {
  std::cout << '"';
  for (unsigned char byte : value) {
    switch (byte) {
      case '"':
        std::cout << "\\\"";
        break;
      case '\\':
        std::cout << "\\\\";
        break;
      case '\b':
        std::cout << "\\b";
        break;
      case '\f':
        std::cout << "\\f";
        break;
      case '\n':
        std::cout << "\\n";
        break;
      case '\r':
        std::cout << "\\r";
        break;
      case '\t':
        std::cout << "\\t";
        break;
      default:
        if (byte < 0x20) {
          std::cout << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                    << static_cast<int>(byte) << std::dec << std::setfill(' ');
        } else {
          std::cout << static_cast<char>(byte);
        }
    }
  }
  std::cout << '"';
}

}  // namespace

int main(int argc, char **argv) {
  if (argc != 2) {
    std::cerr << "usage: lstmf_info FILE.lstmf\n";
    return 2;
  }

  const std::string filename = argv[1];
  tesseract::DocumentData document(filename);
  if (!document.LoadDocument(filename.c_str(), 0,
                             std::numeric_limits<int64_t>::max(), nullptr)) {
    std::cerr << "failed to load lstmf: " << filename << '\n';
    return 3;
  }

  const int samples = document.NumPages();
  std::cout << "{\"samples\":" << samples << ",\"texts\":[";
  for (int index = 0; index < samples; ++index) {
    const tesseract::ImageData *page = document.GetPage(index);
    if (page == nullptr) {
      std::cerr << "missing sample " << index << " in " << filename << '\n';
      return 4;
    }
    if (index > 0) {
      std::cout << ',';
    }
    WriteJsonString(page->transcription());
  }
  std::cout << "]}\n";
  return 0;
}
