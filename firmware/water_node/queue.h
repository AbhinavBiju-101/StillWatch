// Flash-backed queue of unsent readings. Survives reboots and power cuts; the server's (device_id, ts)
// duplicate check makes re-sending after a crash harmless, so the design favours "never lose a reading" over
// "never send twice".
//
// On flash there are two files: an append-only log of fixed-size Rec records, and a tiny file with the
// number of records already uploaded. Records are only ever appended (cheap on flash); the log is rewritten
// only when many consumed records have piled up at its front.
//
// S is the storage: a thin wrapper over LittleFS on the device, an in-memory map in the host tests.
//   size_t size(path)                       bytes in the file, 0 if missing
//   bool   append(path, data, n)
//   bool   readAt(path, offset, buf, n)     false if it cannot read exactly n bytes
//   bool   replace(path, data, n)           write the whole (small) file
//   bool   rename(from, to)                 replaces `to`
//   void   remove(path)
#pragma once
#include "logic.h"

template <class S, uint32_t CAPACITY, uint32_t COMPACT_AT = 400>
class PersistentQueue {
 public:
  explicit PersistentQueue(S &storage) : fs_(storage) {}
  uint32_t dropped = 0;                       // records lost to a full queue or corruption since boot

  void begin() {
    size_t sz = fs_.size(LOG);
    total_ = (uint32_t)(sz / sizeof(Rec));
    head_ = readHead();
    if (head_ > total_) head_ = 0;            // a meta file that makes no sense: resend rather than lose
    if (sz % sizeof(Rec)) compact();          // power cut mid-append: drop the torn tail
  }

  uint32_t count() const { return total_ - head_; }

  bool push(Rec &r) {
    recSeal(r);
    if (count() >= CAPACITY) { head_++; dropped++; }      // full: the oldest reading goes
    if (!fs_.append(LOG, &r, sizeof r)) return false;
    total_++;
    if (head_ >= COMPACT_AT) compact();
    return true;
  }

  // Copies up to `max` valid records from the front into out[]. Corrupt ones are skipped and counted.
  // Does not remove anything: call pop(n) once the server has accepted them.
  int peek(Rec *out, int max) {
    int n = 0;
    uint32_t i = head_, skipped = 0;
    while (i < total_ && n < max) {
      Rec r;
      if (fs_.readAt(LOG, (size_t)i * sizeof(Rec), &r, sizeof r) && recOk(r)) {
        if (skipped) { head_ = i; dropped += skipped; writeHead(); skipped = 0; }   // step over the bad ones
        out[n++] = r;
        i++;
      } else {
        if (n > 0) break;                     // deliver what is good first; the bad one is handled next call
        i++; skipped++;
      }
    }
    if (skipped) { head_ = i; dropped += skipped; writeHead(); }
    if (head_ >= total_) clear();
    return n;
  }

  // Removes the first n records (those peek() returned, plus any corrupt ones in front of them).
  void pop(int n) {
    head_ += (uint32_t)n;
    if (head_ >= total_) { clear(); return; }
    writeHead();
    if (head_ >= COMPACT_AT) compact();
  }

  void clearAll() { clear(); }

 private:
  S &fs_;
  uint32_t head_ = 0, total_ = 0;
  static constexpr const char *LOG = "/q.log", *META = "/q.head", *TMP = "/q.tmp";

  uint32_t readHead() {
    uint32_t m[2];
    if (fs_.size(META) != sizeof m || !fs_.readAt(META, 0, m, sizeof m) || m[0] != ~m[1]) return 0;
    return m[0];
  }
  void writeHead() {
    uint32_t m[2] = {head_, ~head_};
    fs_.replace(META, m, sizeof m);
  }
  void clear() {
    fs_.remove(LOG);
    fs_.remove(META);
    head_ = total_ = 0;
  }

  // Rewrite the log without its consumed prefix (and without a torn tail). Crash-safe: the head file is set
  // to 0 BEFORE the swap, so a crash in between only means some records are sent twice, never lost.
  void compact() {
    uint32_t keep = total_ - head_;
    if (keep == 0) { clear(); return; }
    fs_.remove(TMP);
    Rec buf[8];
    for (uint32_t done = 0; done < keep;) {
      uint32_t k = keep - done < 8 ? keep - done : 8;
      if (!fs_.readAt(LOG, (size_t)(head_ + done) * sizeof(Rec), buf, k * sizeof(Rec)) ||
          !fs_.append(TMP, buf, k * sizeof(Rec))) {
        fs_.remove(TMP);
        return;                               // leave the old log alone; try again later
      }
      done += k;
    }
    uint32_t zero[2] = {0, ~0u};
    if (!fs_.replace(META, zero, sizeof zero)) { fs_.remove(TMP); return; }
    if (!fs_.rename(TMP, LOG)) { fs_.remove(TMP); return; }
    head_ = 0;
    total_ = keep;
  }
};
