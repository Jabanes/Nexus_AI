// AudioWorkletProcessor for Moshi playback
// Based on typical ring buffer implementation + adaptive jitter buffering

// In AudioWorkletGlobalScope, 'sampleRate' is a global variable.
function asMs(samples) {
    return (samples * 1000 / sampleRate).toFixed(1);
}

function asSamples(mili) {
    return Math.round(mili * sampleRate / 1000);
}

class MoshiProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        // Buffer length definitions
        // We target 80ms chunks typical for Opus/Moshi? 
        // Actually the user code used 80ms frameSize.
        let frameSize = asSamples(80);

        // Initial buffering before playback starts
        this.initialBufferSamples = 1 * frameSize;

        // Additional partial buffer margin
        this.partialBufferSamples = asSamples(10);

        // Max buffer size before dropping packets (latency control)
        this.maxBufferSamples = asSamples(10);

        // Increments for adaptive buffering
        this.partialBufferIncrement = asSamples(5);
        this.maxPartialWithIncrements = asSamples(80);
        this.maxBufferSamplesIncrement = asSamples(5);
        this.maxMaxBufferWithIncrements = asSamples(80);

        // State
        this.initState();

        this.port.onmessage = (event) => {
            if (event.data.type === "reset") {
                this.initState();
                return;
            }

            if (event.data.type === "push") {
                // Receive PCM Float32Array
                const pcm = event.data.pcm;
                // pcm should be Float32Array (channel data) or array of channels
                // The worker sends { type: 'pcm', pcm: Float32Array } usually mono

                // Store frames
                // We'll assume mono for now as per previous code
                this.frames.push(pcm);

                if (this.currentSamples() >= this.initialBufferSamples && !this.started) {
                    this.start();
                }

                // Adaptive buffer logic (drop if too much latency)
                if (this.currentSamples() >= this.totalMaxBufferSamples()) {
                    let target = this.initialBufferSamples + this.partialBufferSamples;
                    while (this.currentSamples() > target) {
                        if (this.frames.length === 0) break;
                        let first = this.frames[0];
                        let to_remove = this.currentSamples() - target;
                        to_remove = Math.min(first.length - this.offsetInFirstBuffer, to_remove);

                        this.offsetInFirstBuffer += to_remove;
                        this.timeInStream += to_remove / sampleRate;

                        if (this.offsetInFirstBuffer >= first.length) {
                            this.frames.shift();
                            this.offsetInFirstBuffer = 0;
                        }
                    }
                    // Auto-increase buffer size if we are hitting limits often
                    this.maxBufferSamples = Math.min(this.maxMaxBufferWithIncrements, this.maxBufferSamples + this.maxBufferSamplesIncrement);
                }
            }
        };
    }

    initState() {
        this.frames = [];
        this.offsetInFirstBuffer = 0;
        this.firstOut = false;
        this.remainingPartialBufferSamples = 0;
        this.timeInStream = 0.0;
        this.started = false;

        this.totalAudioPlayed = 0.0;
        this.actualAudioPlayed = 0.0;

        this.partialBufferSamples = asSamples(10);
        this.maxBufferSamples = asSamples(10);
    }

    totalMaxBufferSamples() {
        return this.maxBufferSamples + this.partialBufferSamples + this.initialBufferSamples;
    }

    currentSamples() {
        let samples = 0;
        for (const frame of this.frames) {
            samples += frame.length;
        }
        samples -= this.offsetInFirstBuffer;
        return samples;
    }

    start() {
        this.started = true;
        this.remainingPartialBufferSamples = this.partialBufferSamples;
        this.firstOut = true;
    }

    canPlay() {
        return this.started && this.frames.length > 0 && this.remainingPartialBufferSamples <= 0;
    }

    process(inputs, outputs, parameters) {
        console.log("[WORKLET PROCESS]", {
            framesRequested: outputs[0][0].length,
            bufferAvailable: this.currentSamples()
        });

        // We assume 1 output, 1 channel (or copy mono to all channels)
        const output = outputs[0];
        const channel0 = output[0]; // Left/Mono channel
        // If stereo output, we might want to copy to channel 1 too, but let's stick to simple logic

        if (!channel0) return true;

        const bufferSize = channel0.length;

        if (!this.canPlay()) {
            // Not playing yet (buffering or underrun)
            // Output silence is default
            if (this.actualAudioPlayed > 0) {
                // We were playing, so this is an underrun/buffering gap
                this.totalAudioPlayed += bufferSize / sampleRate;
            }
            this.remainingPartialBufferSamples -= bufferSize;
            return true;
        }

        let out_idx = 0;
        while (out_idx < bufferSize && this.frames.length > 0) {
            let first = this.frames[0];
            let availableInFirst = first.length - this.offsetInFirstBuffer;
            let needed = bufferSize - out_idx;
            let to_copy = Math.min(availableInFirst, needed);

            // Copy data
            channel0.set(first.subarray(this.offsetInFirstBuffer, this.offsetInFirstBuffer + to_copy), out_idx);

            this.offsetInFirstBuffer += to_copy;
            out_idx += to_copy;

            if (this.offsetInFirstBuffer >= first.length) {
                this.frames.shift();
                this.offsetInFirstBuffer = 0;
            }
        }

        // Ramp up volume on first packet to avoid click?
        // Original code did some fading.
        if (this.firstOut) {
            this.firstOut = false;
            for (let i = 0; i < out_idx; i++) {
                channel0[i] *= i / out_idx;
            }
        }

        // If we ran out of data during this block
        if (out_idx < bufferSize) {
            // Underrun
            // Ramp down
            for (let i = 0; i < out_idx; i++) {
                channel0[i] *= (out_idx - i) / out_idx;
            }

            // Increase buffering for next time
            this.partialBufferSamples = Math.min(this.maxPartialWithIncrements, this.partialBufferSamples + this.partialBufferIncrement);

            this.resetStart(); // Go back to buffering state
        }

        this.totalAudioPlayed += bufferSize / sampleRate;
        this.actualAudioPlayed += out_idx / sampleRate;
        this.timeInStream += out_idx / sampleRate;

        return true;
    }

    resetStart() {
        this.started = false;
    }
}

registerProcessor("moshi-processor", MoshiProcessor);
