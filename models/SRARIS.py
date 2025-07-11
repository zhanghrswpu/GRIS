import tensorflow as tf
import os, pdb
import sys
sys.path.append('./models/')
from PairWise_model import Base_CF
import numpy as np
import tensorflow.keras.backend as K
import pdb

def kernel_matrix(x, sigma):
    return K.exp((tf.matmul(x, tf.transpose(x)) - 1) / sigma)

def hsic(Kx, Ky, m):
    Kxy = K.dot(Kx, Ky)
    h = tf.linalg.trace(Kxy) / m ** 2 + K.mean(Kx) * K.mean(Ky) - \
        2 * K.mean(Kxy) / m
    return h * (m / (m - 1)) ** 2

def feature_loss(x, C=2):
    base_loss = (2 / (1 + tf.exp(-x / C)) - 1) ** 2
    return base_loss

class SRARIS(Base_CF):
    def __init__(self, args, dataset):
        super(SRARIS, self).__init__(args)
        self.gcn_layer = args.gcn_layer
        self.sigma = args.sigma
        self.beta = args.beta
        self.feature = args.feature
        self.num_inter = len(dataset.training_user)*2
        self.adj_indices, self.adj_values, adj_shape = dataset.convert_csr_to_sparse_tensor_inputs(dataset.uu_i_matrix)
        self.social_index = dataset.social_index_in_social_lightgcn()
        self.social_u = self.adj_indices[self.social_index, 0]#.reshape(-1,)
        self.social_v = self.adj_indices[self.social_index, 1]#.reshape(-1,)
        #np.save('social_index.npy', self.adj_indices[self.social_index])
        self.social_weight = self.adj_values[self.social_index]
        self.adj_matrix = tf.SparseTensor(self.adj_indices, self.adj_values, adj_shape)
        self.Mask_MLP1 = tf.layers.Dense(self.latent_dim, activation=tf.nn.relu)
        self.Mask_MLP2 = tf.layers.Dense(1, activation=None)
        self.Mask_MLP3 = tf.layers.Dense(self.latent_dim, activation=tf.nn.relu)
        self.Mask_MLP4 = tf.layers.Dense(1, activation=None)
        self.Mask_MLP5 = tf.layers.Dense(self.latent_dim, activation=tf.nn.relu)
        self.Mask_MLP6 = tf.layers.Dense(1, activation=None)
        self.Mask_MLP7 = tf.layers.Dense(self.latent_dim, activation=tf.nn.relu)
        self.Mask_MLP8 = tf.layers.Dense(1, activation=None)
        self.Mask_MLP9 = tf.layers.Dense(self.latent_dim, activation=tf.nn.relu)
        self.Mask_MLP10 = tf.layers.Dense(1, activation=None)
        self.edge_bias = args.edge_bias
        self._build_graph()
    
    def graph_reconstruction(self, ego_emb, layer):
        row, col = self.social_u, self.social_v
        row_emb = tf.gather_nd(ego_emb, row)
        col_emb = tf.gather_nd(ego_emb, col)
        cat_emb = tf.concat([row_emb, col_emb], axis=1)  # [n, 2d] 
        if layer == 0:
            logit = self.Mask_MLP2(self.Mask_MLP1(cat_emb))
        elif layer == 1:
            logit = self.Mask_MLP4(self.Mask_MLP3(cat_emb))
        elif layer == 2:
            logit = self.Mask_MLP6(self.Mask_MLP5(cat_emb))
        elif layer == 3:
            logit = self.Mask_MLP8(self.Mask_MLP7(cat_emb))
        elif layer == 4:
            logit = self.Mask_MLP10(self.Mask_MLP9(cat_emb))
        # logit：MLP输出的结果，它是一个未经激活函数处理的原始分数，表示信息传播的可能性。
        logit = tf.reshape(logit, [-1, ])
        eps = tf.random_uniform(logit.shape) #(bias - (1 - bias)) * tf.random_uniform(logit.shape) + (1 - bias)
        mask_gate_input = tf.log(eps) - tf.log(1 - eps)
        mask_gate_input = (logit+mask_gate_input) / 0.2
        # 将上述结果通过sigmoid函数处理，并加上一个偏置项 self.edge_bias，得到最终的社交关系权重。
        mask_gate_input = tf.nn.sigmoid(mask_gate_input) + self.edge_bias
        original_weight = self.social_weight  # 原始社交权重
        # 将原始社交关系的权重 self.social_weight 与计算出的社交关系权重 mask_gate_input 相乘，得到调整后的权重
        masked_values = tf.multiply(self.social_weight, mask_gate_input)
        weight_difference = masked_values - original_weight  # 差值
        # 使用 tf.scatter_nd_update 函数将调整后的权重更新到原始社交图的相应位置。
        masked_values_all = tf.scatter_nd_update(tf.Variable(self.adj_values, trainable=False),
                                                 tf.reshape(self.social_index, [-1, 1]),
                                                 masked_values)
        # 生成一个新的稀疏矩阵，表示去噪后的社交图，其中包含了调整后的社交关系权重。
        masked_adj_matrix = tf.SparseTensor(self.adj_matrix.indices, masked_values_all, self.adj_matrix.shape)
        return masked_adj_matrix, tf.reduce_mean(mask_gate_input), mask_gate_input, weight_difference

    def _create_lightgcn_emb(self, ego_emb):
        all_emb = [ego_emb]
        for _ in range(self.gcn_layer):
            tmp_emb = tf.sparse.sparse_dense_matmul(self.adj_matrix, all_emb[-1])
            all_emb.append(tmp_emb)
        all_emb = tf.stack(all_emb, axis=1)
        mean_emb = tf.reduce_mean(all_emb, axis=1, keepdims=False)
        # out_user_emb, out_item_emb = tf.split(mean_emb, [self.num_user, self.num_item], axis=0)
        return mean_emb #out_user_emb, out_item_emb


    def _create_masked_lightgcn_emb(self, ego_emb, masked_adj_matrix):
        all_emb = [ego_emb]
        all_emb_masked = [ego_emb]
        for _ in range(self.gcn_layer):
            tmp_emb = tf.sparse.sparse_dense_matmul(self.adj_matrix, all_emb[-1])
            all_emb.append(tmp_emb)
            cur_emb = tf.sparse.sparse_dense_matmul(masked_adj_matrix, all_emb_masked[-1])
            all_emb_masked.append(cur_emb)
        all_emb = tf.stack(all_emb, axis=1)
        mean_emb = tf.reduce_mean(all_emb, axis=1, keepdims=False)
        out_user_emb, out_item_emb = tf.split(mean_emb, [self.num_user, self.num_item], axis=0)
        all_emb_masked = tf.stack(all_emb_masked, axis=1)
        mean_emb_masked = tf.reduce_mean(all_emb_masked, axis=1, keepdims=False)
        out_user_emb_masked, out_item_emb_masked = tf.split(mean_emb_masked, [self.num_user, self.num_item], axis=0)
        return out_user_emb, out_item_emb, out_user_emb_masked, out_item_emb_masked


    def _create_multiple_masked_lightgcn_emb(self, ego_emb):
        all_emb = [ego_emb]
        all_emb_masked = [ego_emb]
        for i in range(self.gcn_layer):
            masked_adj_matrix, _ = self.graph_reconstruction(all_emb[-1], i)  # each masked layer
            tmp_emb = tf.sparse.sparse_dense_matmul(self.adj_matrix, all_emb[-1])
            all_emb.append(tmp_emb)
            cur_emb = tf.sparse.sparse_dense_matmul(masked_adj_matrix, all_emb_masked[-1])
            all_emb_masked.append(cur_emb)
        all_emb = tf.stack(all_emb, axis=1)
        mean_emb = tf.reduce_mean(all_emb, axis=1, keepdims=False)
        out_user_emb, out_item_emb = tf.split(mean_emb, [self.num_user, self.num_item], axis=0)
        all_emb_masked = tf.stack(all_emb_masked, axis=1)
        mean_emb_masked = tf.reduce_mean(all_emb_masked, axis=1, keepdims=False)
        out_user_emb_masked, out_item_emb_masked = tf.split(mean_emb_masked, [self.num_user, self.num_item], axis=0)
        return out_user_emb, out_item_emb, out_user_emb_masked, out_item_emb_masked


    def _build_graph(self):
        with tf.name_scope('forward'):
            ego_emb = tf.concat([self.user_latent_emb, self.item_latent_emb], axis=0)
            # gcn_emb = self._create_lightgcn_emb(ego_emb)
            self.masked_adj_matrix, self.masked_gate_input, self.masked_values, self.weight_diff = self.graph_reconstruction(ego_emb, 0)
            #### (1) single mask layer  ####
            self.user_emb_old, self.item_emb_old, self.user_emb, self.item_emb = \
                self._create_masked_lightgcn_emb(ego_emb, self.masked_adj_matrix)
            #### (2) multiple mask layer  ###
            # self.user_emb_old, self.item_emb_old, self.user_emb, self.item_emb = \
            #     self._create_multiple_masked_lightgcn_emb(ego_emb)
        with tf.name_scope('optimization'):
            self.ranking_loss, self.regu_loss, self.auc = self.compute_bpr_loss(self.user_emb, self.item_emb)
            self.IB_loss = self.HSIC_Graph() * self.beta
            masked_adj_matrix, mean_gate_input, gate_input, weight_diff = self.graph_reconstruction(ego_emb, 0)
            self.SRA_loss = feature_loss(weight_diff) * self.feature
            self.loss = self.ranking_loss + self.regu_loss + self.IB_loss
            self.opt = tf.train.AdamOptimizer(learning_rate=self.lr).minimize(self.loss)


    def HSIC_Graph(self):
        # users = self.users
        # items = self.pos_items
        users = tf.unique(self.users)[0]
        items = tf.unique(self.pos_items)[0]
        input_x = tf.nn.embedding_lookup(self.user_emb_old, users)
        input_y = tf.nn.embedding_lookup(self.user_emb, users)
        input_x = tf.nn.l2_normalize(input_x, 1)
        input_y = tf.nn.l2_normalize(input_y, 1)
        Kx = kernel_matrix(input_x, self.sigma)
        Ky = kernel_matrix(input_y, self.sigma)
        loss_user = hsic(Kx, Ky, self.batch_size)
        ### item part ###
        input_x = tf.nn.embedding_lookup(self.item_emb_old, items)
        input_y = tf.nn.embedding_lookup(self.item_emb, items)
        input_x = tf.nn.l2_normalize(input_x, 1)
        input_y = tf.nn.l2_normalize(input_y, 1)
        Kx = kernel_matrix(input_x, self.sigma)
        Ky = kernel_matrix(input_y, self.sigma)
        loss_item = hsic(Kx, Ky, self.batch_size)
        loss = loss_user + loss_item
        return loss
