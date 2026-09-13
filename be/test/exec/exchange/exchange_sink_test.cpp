// Licensed to the Apache Software Foundation (ASF) under one
// or more contributor license agreements.  See the NOTICE file
// distributed with this work for additional information
// regarding copyright ownership.  The ASF licenses this file
// to you under the Apache License, Version 2.0 (the
// "License"); you may not use this file except in compliance
// with the License.  You may obtain a copy of the License at
//
//   http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing,
// software distributed under the License is distributed on an
// "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
// KIND, either express or implied.  See the License for the
// specific language governing permissions and limitations
// under the License.

#include "exec/exchange/exchange_sink_test.h"

#include <gtest/gtest.h>

#include <memory>

#include "exec/operator/exchange_sink_buffer.h"

namespace doris {

TEST_F(ExchangeSinkTest, test_normal_end) {
    {
        auto state = std::make_shared<MockRuntimeState>();
        auto buffer = create_buffer(state);

        auto sink1 = create_sink(state, buffer);
        auto sink2 = create_sink(state, buffer);
        auto sink3 = create_sink(state, buffer);

        EXPECT_EQ(sink1.add_block(dest_ins_id_1, true), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_2, true), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_3, true), Status::OK());

        EXPECT_EQ(sink2.add_block(dest_ins_id_1, true), Status::OK());
        EXPECT_EQ(sink2.add_block(dest_ins_id_2, true), Status::OK());
        EXPECT_EQ(sink2.add_block(dest_ins_id_3, true), Status::OK());

        EXPECT_EQ(sink3.add_block(dest_ins_id_1, true), Status::OK());
        EXPECT_EQ(sink3.add_block(dest_ins_id_2, true), Status::OK());
        EXPECT_EQ(sink3.add_block(dest_ins_id_3, true), Status::OK());

        for (const auto& [id, instance] : buffer->_rpc_instances) {
            EXPECT_EQ(instance->running_sink_count, 3) << "id : " << id;
        }

        for (const auto& [id, instance] : buffer->_rpc_instances) {
            EXPECT_EQ(instance->rpc_channel_is_turn_off, false) << "id : " << id;
        }

        pop_block(dest_ins_id_1, PopState::accept);
        pop_block(dest_ins_id_1, PopState::accept);
        pop_block(dest_ins_id_1, PopState::accept);

        pop_block(dest_ins_id_2, PopState::accept);
        pop_block(dest_ins_id_2, PopState::accept);
        pop_block(dest_ins_id_2, PopState::accept);

        pop_block(dest_ins_id_3, PopState::accept);
        pop_block(dest_ins_id_3, PopState::accept);
        pop_block(dest_ins_id_3, PopState::accept);

        for (const auto& [id, instance] : buffer->_rpc_instances) {
            EXPECT_EQ(instance->running_sink_count, 0) << "id : " << id;
        }

        for (const auto& [id, instance] : buffer->_rpc_instances) {
            EXPECT_EQ(instance->rpc_channel_is_turn_off, true) << "id : " << id;
        }
        clear_all_done();
    }
}

TEST_F(ExchangeSinkTest, test_eof_end) {
    {
        auto state = std::make_shared<MockRuntimeState>();
        auto buffer = create_buffer(state);

        auto sink1 = create_sink(state, buffer);
        auto sink2 = create_sink(state, buffer);
        auto sink3 = create_sink(state, buffer);

        EXPECT_EQ(sink1.add_block(dest_ins_id_1, false), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_2, false), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_3, false), Status::OK());

        EXPECT_EQ(sink2.add_block(dest_ins_id_1, true), Status::OK());
        EXPECT_EQ(sink2.add_block(dest_ins_id_2, true), Status::OK());
        EXPECT_EQ(sink2.add_block(dest_ins_id_3, true), Status::OK());

        EXPECT_EQ(sink3.add_block(dest_ins_id_1, false), Status::OK());
        EXPECT_EQ(sink3.add_block(dest_ins_id_2, true), Status::OK());
        EXPECT_EQ(sink3.add_block(dest_ins_id_3, false), Status::OK());

        for (const auto& [id, instance] : buffer->_rpc_instances) {
            EXPECT_EQ(instance->running_sink_count, 3) << "id : " << id;
        }

        for (const auto& [id, instance] : buffer->_rpc_instances) {
            EXPECT_EQ(instance->rpc_channel_is_turn_off, false) << "id : " << id;
        }

        pop_block(dest_ins_id_1, PopState::eof);
        EXPECT_EQ(buffer->_rpc_instances[dest_ins_id_1]->rpc_channel_is_turn_off, true);
        EXPECT_TRUE(buffer->_rpc_instances[dest_ins_id_1]->package_queue.empty());

        pop_block(dest_ins_id_2, PopState::accept);
        pop_block(dest_ins_id_2, PopState::accept);
        pop_block(dest_ins_id_2, PopState::accept);

        pop_block(dest_ins_id_3, PopState::accept);
        pop_block(dest_ins_id_3, PopState::accept);
        pop_block(dest_ins_id_3, PopState::accept);

        EXPECT_EQ(buffer->_rpc_instances[dest_ins_id_1]->rpc_channel_is_turn_off, true);
        EXPECT_EQ(buffer->_rpc_instances[dest_ins_id_2]->rpc_channel_is_turn_off, false)
                << "not all eos";
        EXPECT_EQ(buffer->_rpc_instances[dest_ins_id_3]->rpc_channel_is_turn_off, false)
                << " not all eos";

        EXPECT_TRUE(sink1.add_block(dest_ins_id_1, true).is<ErrorCode::END_OF_FILE>());
        EXPECT_EQ(sink1.add_block(dest_ins_id_2, true), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_3, true), Status::OK());
        pop_block(dest_ins_id_2, PopState::accept);
        pop_block(dest_ins_id_3, PopState::accept);

        EXPECT_EQ(buffer->_rpc_instances[dest_ins_id_1]->rpc_channel_is_turn_off, true);
        EXPECT_EQ(buffer->_rpc_instances[dest_ins_id_2]->rpc_channel_is_turn_off, true);
        EXPECT_EQ(buffer->_rpc_instances[dest_ins_id_3]->rpc_channel_is_turn_off, false);
        EXPECT_EQ(buffer->_rpc_instances[dest_ins_id_3]->running_sink_count, 1);

        clear_all_done();
    }
}

TEST_F(ExchangeSinkTest, test_error_end) {
    {
        auto state = std::make_shared<MockRuntimeState>();
        auto buffer = create_buffer(state);

        auto sink1 = create_sink(state, buffer);
        auto sink2 = create_sink(state, buffer);
        auto sink3 = create_sink(state, buffer);

        EXPECT_EQ(sink1.add_block(dest_ins_id_1, false), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_2, false), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_3, false), Status::OK());

        EXPECT_EQ(sink2.add_block(dest_ins_id_1, false), Status::OK());
        EXPECT_EQ(sink2.add_block(dest_ins_id_2, false), Status::OK());
        EXPECT_EQ(sink2.add_block(dest_ins_id_3, false), Status::OK());

        EXPECT_EQ(sink3.add_block(dest_ins_id_1, false), Status::OK());
        EXPECT_EQ(sink3.add_block(dest_ins_id_2, false), Status::OK());
        EXPECT_EQ(sink3.add_block(dest_ins_id_3, false), Status::OK());

        for (const auto& [id, instance] : buffer->_rpc_instances) {
            EXPECT_EQ(instance->running_sink_count, 3) << "id : " << id;
        }

        for (const auto& [id, instance] : buffer->_rpc_instances) {
            EXPECT_EQ(instance->rpc_channel_is_turn_off, false) << "id : " << id;
        }

        pop_block(dest_ins_id_2, PopState::error);

        auto orgin_queue_1_size = done_map[dest_ins_id_1].size();
        auto orgin_queue_2_size = done_map[dest_ins_id_2].size();
        auto orgin_queue_3_size = done_map[dest_ins_id_3].size();

        EXPECT_EQ(sink1.add_block(dest_ins_id_1, false), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_2, false), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_3, false), Status::OK());

        EXPECT_EQ(sink2.add_block(dest_ins_id_1, false), Status::OK());
        EXPECT_EQ(sink2.add_block(dest_ins_id_2, false), Status::OK());
        EXPECT_EQ(sink2.add_block(dest_ins_id_3, false), Status::OK());

        EXPECT_EQ(sink3.add_block(dest_ins_id_1, false), Status::OK());
        EXPECT_EQ(sink3.add_block(dest_ins_id_2, false), Status::OK());
        EXPECT_EQ(sink3.add_block(dest_ins_id_3, false), Status::OK());

        EXPECT_EQ(orgin_queue_1_size, done_map[dest_ins_id_1].size());
        EXPECT_EQ(orgin_queue_2_size, done_map[dest_ins_id_2].size());
        EXPECT_EQ(orgin_queue_3_size, done_map[dest_ins_id_3].size());

        clear_all_done();
    }
}

TEST_F(ExchangeSinkTest, test_queue_size) {
    {
        auto state = std::make_shared<MockRuntimeState>();
        auto buffer = create_buffer(state);

        auto sink1 = create_sink(state, buffer);

        EXPECT_EQ(sink1.add_block(dest_ins_id_1, false), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_1, false), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_1, false), Status::OK());

        EXPECT_EQ(sink1.add_block(dest_ins_id_2, false), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_2, false), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_2, false), Status::OK());

        EXPECT_EQ(sink1.add_block(dest_ins_id_3, false), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_3, false), Status::OK());
        EXPECT_EQ(sink1.add_block(dest_ins_id_3, false), Status::OK());

        std::cout << "queue size : " << buffer->_total_queue_size << "\n";

        EXPECT_EQ(buffer->_total_queue_size, 6);

        std::cout << "each queue size : \n" << buffer->debug_each_instance_queue_size() << "\n";

        pop_block(dest_ins_id_2, PopState::eof);

        std::cout << "queue size : " << buffer->_total_queue_size << "\n";

        EXPECT_EQ(buffer->_total_queue_size, 4);

        std::cout << "each queue size : \n" << buffer->debug_each_instance_queue_size() << "\n";

        EXPECT_EQ(buffer->_rpc_instances[dest_ins_id_1]->rpc_channel_is_turn_off, false);
        EXPECT_EQ(buffer->_rpc_instances[dest_ins_id_2]->rpc_channel_is_turn_off, true);
        EXPECT_EQ(buffer->_rpc_instances[dest_ins_id_3]->rpc_channel_is_turn_off, false);
        clear_all_done();
    }
}

TEST_F(ExchangeSinkTest, ReentrantRpcPreservesCompletedResponse) {
    using Callback = ExchangeSendCallback<PTransmitDataResult>;
    using Closure = AutoReleaseClosure<PTransmitDataParams, Callback>;

    auto state = std::make_shared<MockRuntimeState>();
    auto buffer = create_buffer(state);
    auto sink = create_sink(state, buffer);
    auto channel = sink.channels[dest_ins_id_1];
    auto* instance = buffer->_rpc_instances[dest_ins_id_1].get();
    auto callback = channel->get_send_callback(instance, false);
    callback->start_rpc_time = 0;
    Status::OK().to_protobuf(callback->response_->mutable_status());
    std::shared_ptr<Callback> next_callback;
    int callbacks = 0;
    int reported_errors = 0;

    callback->addSuccessHandler([&](RpcInstance* ins, const bool& eos,
                                    const PTransmitDataResult& response, const int64_t&) {
        ++callbacks;
        // Model the next RPC completing while the previous success callback is on the stack.
        next_callback = channel->get_send_callback(ins, true);
        next_callback->start_rpc_time = 0;
        Status::InternalError("second RPC response")
                .to_protobuf(next_callback->response_->mutable_status());
        EXPECT_FALSE(eos);
        EXPECT_TRUE(Status::create(response.status()).ok());
        EXPECT_TRUE(Status::create(callback->response_->status()).ok());
    });
    callback->addFailedHandler([&](RpcInstance*, const std::string&) { ++reported_errors; });

    auto closure = Closure::create_unique(std::make_shared<PTransmitDataParams>(), callback);
    closure.release()->Run();
    EXPECT_EQ(callbacks, 1);
    EXPECT_EQ(reported_errors, 0);
    ASSERT_NE(next_callback, nullptr);
    EXPECT_TRUE(Status::create(callback->response_->status()).ok());
    EXPECT_TRUE(Status::create(next_callback->response_->status()).is<ErrorCode::INTERNAL_ERROR>());

    int next_callbacks = 0;
    next_callback->addSuccessHandler([&](RpcInstance* ins, const bool& eos,
                                         const PTransmitDataResult& response, const int64_t&) {
        ++next_callbacks;
        EXPECT_EQ(ins, instance);
        EXPECT_TRUE(eos);
        EXPECT_TRUE(Status::create(response.status()).is<ErrorCode::INTERNAL_ERROR>());
        EXPECT_TRUE(Status::create(callback->response_->status()).ok());
    });
    next_callback->addFailedHandler([&](RpcInstance*, const std::string&) { ++reported_errors; });
    auto next_closure =
            Closure::create_unique(std::make_shared<PTransmitDataParams>(), next_callback);
    next_closure.release()->Run();
    EXPECT_EQ(next_callbacks, 1);
    EXPECT_EQ(reported_errors, 0);
}

TEST_F(ExchangeSinkTest, ReentrantRpcPreservesCompletedController) {
    using Callback = ExchangeSendCallback<PTransmitDataResult>;
    using Closure = AutoReleaseClosure<PTransmitDataParams, Callback>;

    auto state = std::make_shared<MockRuntimeState>();
    auto buffer = create_buffer(state);
    auto sink = create_sink(state, buffer);
    auto channel = sink.channels[dest_ins_id_1];
    auto* instance = buffer->_rpc_instances[dest_ins_id_1].get();
    auto callback = channel->get_send_callback(instance, false);
    callback->start_rpc_time = 0;
    std::shared_ptr<Callback> next_callback;
    int callbacks = 0;
    int reported_errors = 0;

    callback->addSuccessHandler(
            [&](RpcInstance* ins, const bool& eos, const PTransmitDataResult&, const int64_t&) {
                ++callbacks;
                next_callback = channel->get_send_callback(ins, true);
                next_callback->start_rpc_time = 0;
                next_callback->cntl_->SetFailed("second RPC transport failure");
                EXPECT_FALSE(eos);
                EXPECT_FALSE(callback->cntl_->Failed());
                EXPECT_TRUE(next_callback->cntl_->Failed());
            });
    callback->addFailedHandler([&](RpcInstance*, const std::string&) { ++reported_errors; });

    auto closure = Closure::create_unique(std::make_shared<PTransmitDataParams>(), callback);
    closure.release()->Run();
    EXPECT_EQ(callbacks, 1);
    EXPECT_EQ(reported_errors, 0);
    ASSERT_NE(next_callback, nullptr);
    EXPECT_FALSE(callback->cntl_->Failed());
    EXPECT_TRUE(next_callback->cntl_->Failed());

    int next_failures = 0;
    next_callback->addSuccessHandler(
            [&](RpcInstance*, const bool&, const PTransmitDataResult&, const int64_t&) {
                ADD_FAILURE() << "The next RPC's transport failure must reach its failed handler";
            });
    next_callback->addFailedHandler([&](RpcInstance* ins, const std::string& error) {
        ++next_failures;
        EXPECT_EQ(ins, instance);
        EXPECT_NE(error.find("second RPC transport failure"), std::string::npos);
        EXPECT_FALSE(callback->cntl_->Failed());
    });
    auto next_closure =
            Closure::create_unique(std::make_shared<PTransmitDataParams>(), next_callback);
    next_closure.release()->Run();
    EXPECT_EQ(next_failures, 1);
    EXPECT_EQ(reported_errors, 0);
}

TEST_F(ExchangeSinkTest, CompletedRpcReleasesAttachmentBeforeCallback) {
    class AttachmentCallback : public DummyBrpcCallback<PTransmitDataResult> {
    public:
        int calls = 0;
        void call() override {
            ++calls;
            EXPECT_TRUE(cntl_->request_attachment().empty());
            cntl_->request_attachment().append("new attachment from callback");
        }
    };
    using Closure = AutoReleaseClosure<PTransmitDataParams, AttachmentCallback>;
    auto callback = std::make_shared<AttachmentCallback>();
    auto closure = Closure::create_unique(std::make_shared<PTransmitDataParams>(), callback);
    callback->cntl_->request_attachment().append("serialized runtime filter");

    closure.release()->Run();
    EXPECT_EQ(callback->calls, 1);
    EXPECT_EQ(callback->cntl_->request_attachment().to_string(), "new attachment from callback");
}

TEST_F(ExchangeSinkTest, ExpiredCallbackStillReleasesAttachment) {
    using Callback = DummyBrpcCallback<PTransmitDataResult>;
    using Closure = AutoReleaseClosure<PTransmitDataParams, Callback>;
    auto callback = Callback::create_shared();
    auto request = std::make_shared<PTransmitDataParams>();
    auto closure = Closure::create_unique(request, callback);
    auto controller = callback->cntl_;
    controller->request_attachment().append("serialized runtime filter");
    callback.reset();

    closure.release()->Run();
    EXPECT_TRUE(controller->request_attachment().empty());
}

} // namespace doris
