#pragma once

#include <memory>
#include <thread>
#include <type_traits>
#include <unordered_map>

#include <VapourSynth4.h>
#include <VSHelper4.h>

#include <fftw3.h>

using unique_float = std::unique_ptr<float[], decltype(&vsh::vsh_aligned_free)>;
using unique_fftwf_complex = std::unique_ptr<fftwf_complex[], decltype(&vsh::vsh_aligned_free)>;
using unique_VSFrame = std::unique_ptr<VSFrame, decltype(VSAPI::freeFrame)>;

struct DFTTestData final {
    VSNode * node = nullptr;
    VSVideoInfo vi{};
    int sbsize, sosize, tbsize, tosize, swin, twin;
    double sbeta, tbeta;
    float f0beta;
    bool zmean, process[3];
    float srcScale, dstScale;
    int barea, bvolume, ccnt, ccnt2, type, sbd1, inc, peak;
    bool uf0b;
    VSVideoFormat padFormat{}, ebuffFormat{};
    int padWidth[3], padHeight[3], eheight[3];
    unique_float hw{ nullptr, nullptr }, sigmas{ nullptr, nullptr }, sigmas2{ nullptr, nullptr }, pmins{ nullptr, nullptr }, pmaxs{ nullptr, nullptr };
    unique_fftwf_complex dftgc{ nullptr, nullptr };
    std::unique_ptr<fftwf_plan_s, decltype(&fftwf_destroy_plan)> ft{ nullptr, nullptr }, fti{ nullptr, nullptr };
    std::unordered_map<std::thread::id, unique_VSFrame> ebuff;
    std::unordered_map<std::thread::id, unique_float> dftr;
    std::unordered_map<std::thread::id, unique_fftwf_complex> dftc, dftc2;
    void (*copyPad)(const VSFrame * src, VSFrame * dst[3], const DFTTestData * const VS_RESTRICT d, const VSAPI * vsapi) noexcept;
    void (*filterCoeffs)(float * dftc, const float * sigmas, const int ccnt, const float * pmin, const float * pmax, const float * sigmas2) noexcept;
    void (*func_0)(VSFrame * src[3], VSFrame * dst, const DFTTestData * const VS_RESTRICT d, const VSAPI * vsapi) noexcept;
    void (*func_1)(VSFrame * src[15][3], VSFrame * dst, const int pos, const DFTTestData * const VS_RESTRICT d, const VSAPI * vsapi) noexcept;
};
