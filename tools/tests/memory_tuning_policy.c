/* Native policy checks; invoke explicitly after authorizing compilation.
 * SPDX-License-Identifier: BSD-2-Clause-Patent */
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "MemoryTuningPolicy.h"

int main(int argc, char **argv)
{
  unsigned char source[4096], modified[4096], previous[4096];
  RADXA_MEM_REQUEST request, invalid;
  unsigned int offset, index, changed;
  size_t size;
  FILE *input;
  assert(argc == 2);
  assert(sizeof(RADXA_MEM_REQUEST) == 64);
  assert(sizeof(RADXA_MEM_RESULT) == 128);
  assert(offsetof(RADXA_MEM_REQUEST, Crc32) == 60);
  assert(offsetof(RADXA_MEM_RESULT, Crc32) == 124);
  assert(MemTuneCrc32("123456789", 9) == 0xCBF43926U);
  input = fopen(argv[1], "rb");
  assert(input != NULL);
  size = fread(source, 1, sizeof(source), input);
  assert(size > 0 && size < sizeof(source));
  assert(fgetc(input) == EOF);
  fclose(input);
  offset = MemTuneLocate(source, (UINT32)size);
  assert(offset != 0);
  for (index = 0; index < size; index++) {
    assert(MemTuneLocate(source, index) == 0);
  }
  MemTuneInitialize(&request);
  assert(MemTuneRequestValid(&request));
  request.Sequence = 1;
  request.Profile = RADXA_MEM_CUSTOM;
  request.BoardMask = 512;
  request.CapacityMiB = 49152;
  request.ChannelMask = 15;
  request.DeviceWidth = 8;
  request.Ranks = 2;
  request.DdrType = 1;
  request.RateMt = 5500;
  request.Timing[0] = 12;
  MemTuneSeal(&request);
  assert(MemTuneRequestValid(&request));
  memcpy(modified, source, size);
  assert(MemTuneReplace(modified, (UINT32)size, &request));
  assert(MemTuneLocate(modified, (UINT32)size) == offset);
  changed = 0;
  for (index = 0; index < size; index++) {
    if (source[index] != modified[index]) {
      assert(index == offset - 6 || (index >= offset && index < offset + 64));
      changed++;
    }
  }
  assert(changed > 0);
  memcpy(previous, modified, size);
  invalid = request;
  invalid.Revision = 2;
  MemTuneSeal(&invalid);
  assert(!MemTuneReplace(modified, (UINT32)size, &invalid));
  assert(memcmp(previous, modified, size) == 0);
  invalid = request;
  invalid.Timing[2] = 13;
  invalid.Timing[3] = 12;
  MemTuneSeal(&invalid);
  assert(!MemTuneRequestValid(&invalid));
  invalid = request;
  invalid.BoardMask = 3;
  MemTuneSeal(&invalid);
  assert(!MemTuneRequestValid(&invalid));
  modified[offset - 16] ^= 1;
  assert(MemTuneLocate(modified, (UINT32)size) == 0);
  puts("Memory wire layout, bounds, CRC and byte preservation passed.");
  return 0;
}
