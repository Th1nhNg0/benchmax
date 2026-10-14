// Pi 1.2.0 lists Gemini 4 on OpenCode Go, but its provider lacks the Google API
// implementation and its simple adapter does not recognize Gemini 4 thinking levels.
// Keep the built-in catalog, credentials, other APIs, and tool handling unchanged.
import { clampThinkingLevel } from '@earendil-works/pi-ai';
import { googleGenerativeAIApi } from '@earendil-works/pi-ai/api/google-generative-ai.lazy';
import { buildBaseOptions } from '@earendil-works/pi-ai/api/simple-options';
import { resolveGoogleThinkingLevel, toGoogleThinkingLevel } from '@earendil-works/pi-ai/api/google-shared';
import { withOpenCodeSessionHeader } from '@earendil-works/pi-ai/providers/opencode-headers';

export default function (pi) {
  const google = withOpenCodeSessionHeader(googleGenerativeAIApi());
  pi.registerProvider('opencode-go', {
    api: 'google-generative-ai',
    streamSimple(model, context, options) {
      if (model.id !== 'gemini-4-argon-contributor') {
        return google.streamSimple(model, context, options);
      }
      if (!options?.apiKey) throw new Error('No API key for provider: opencode-go');
      const level = clampThinkingLevel(model, options.reasoning ?? 'off');
      return google.stream(model, context, {
        ...buildBaseOptions(model, context, options, options.apiKey),
        toolChoice: options.toolChoice,
        thinking: {
          enabled: true,
          level: toGoogleThinkingLevel(resolveGoogleThinkingLevel(model, level)),
        },
      });
    },
  });
}
