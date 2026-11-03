export { ChatWorkspace } from "./ChatWorkspace";
export type { ChatWorkspaceProps } from "./ChatWorkspace";
export { useChatWorkspace } from "./hooks";
export type { ChatWorkspaceController, UseChatWorkspaceOptions } from "./hooks";
export { LegacyChatError, LegacyChatService, attachmentRefs, fileToAttachmentUpload, parseLegacySseFrames } from "./service";
export { EMPTY_CHAT_STATE, chatReducer, makeUserBlock } from "./reducer";
export type { ChatAttachment, ChatBlock, ChatEvent, ChatItem, ChatProject, ChatState, ChatStatus, PendingTurn } from "./types";
