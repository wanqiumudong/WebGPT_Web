
import PagaStore from"./pageStore"
import UserStore from"./userStore"
import {configureStore} from "@reduxjs/toolkit";

// configureStore创建一个redux数据
const store = configureStore({
    // 合并多个Slice
    reducer: {
        PageState: PagaStore,
        UserState: UserStore,
    },
    middleware: (getDefaultMiddleware) =>
        getDefaultMiddleware({serializableCheck: false})

});

export default store

