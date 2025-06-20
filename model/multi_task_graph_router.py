import random
import numpy as np
import torch
from graph_nn import  form_data,GNN_prediction
from data_processing.utils import savejson,loadjson,savepkl,loadpkl
import pandas as pd
import json
import re
import yaml
device = "cuda" if torch.cuda.is_available() else "cpu"

class graph_router_prediction:
    def __init__(self, router_data_path,llm_path,llm_embedding_path,config,wandb):
        self.config = config
        self.wandb = wandb
        self.data_df = pd.read_csv(router_data_path)
        self.llm_description = loadjson(llm_path)
        self.llm_names = list(self.llm_description.keys())
        self.num_llms=len(self.llm_names)
        self.num_query=int(len(self.data_df)/self.num_llms)
        self.num_task=config['num_task']
        self.set_seed(self.config['seed'])
        self.llm_description_embedding=loadpkl(llm_embedding_path)
        self.prepare_data_for_GNN()
        self.split_data()
        self.form_data = form_data(device)
        self.query_dim = self.query_embedding_list.shape[1]
        self.llm_dim = self.llm_description_embedding.shape[1]
        self.in_edges_size = 1 + self.combined_edge.shape[1]
        self.GNN_predict = GNN_prediction(query_feature_dim=self.query_dim, llm_feature_dim=self.llm_dim,
                                    hidden_features_size=self.config['embedding_dim'], in_edges_size=self.in_edges_size,wandb=self.wandb,config=self.config,device=device)
        print("GNN training successfully initialized.")
        self.train_GNN()


    def set_seed(self,seed):
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed(seed)
            torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False

    def split_data(self):
        self.query_per_task=int(self.num_query/self.num_task)
        split_ratio = self.config['split_ratio']

        # Calculate the size of each set for each task
        train_size = int(self.query_per_task * split_ratio[0])
        val_size = int(self.query_per_task * split_ratio[1])
        test_size = int(self.query_per_task * split_ratio[2])

        # Generate indices
        train_idx = []
        validate_idx = []
        test_idx = []

        for task_id in range(self.num_task):
            # Starting index for each task
            start_idx = task_id * self.query_per_task * self.num_llms

            # Add training set indices
            train_idx.extend(range(start_idx, start_idx + train_size* self.num_llms))

            # Add validation set indices
            validate_idx.extend(range(start_idx + train_size* self.num_llms,
                                      start_idx + train_size* self.num_llms + val_size* self.num_llms))

            # Add test set indices
            test_idx.extend(range(start_idx + train_size* self.num_llms + val_size* self.num_llms,
                                  start_idx + train_size* self.num_llms + val_size* self.num_llms + test_size* self.num_llms))


        self.combined_edge=np.concatenate((self.cost_list.reshape(-1,1),self.effect_list.reshape(-1,1)),axis=1)
        self.scenario=self.config['scenario']
        if self.scenario== "Performance First":
            self.effect_list = 1.0 * self.effect_list - 0.0 * self.cost_list
        elif self.scenario== "Balance":
            self.effect_list = 0.5 * self.effect_list - 0.5 * self.cost_list
        else:
            self.effect_list = 0.2 * self.effect_list - 0.8 * self.cost_list

        effect_re=self.effect_list.reshape(-1,self.num_llms)
        self.label=np.eye(self.num_llms)[np.argmax(effect_re, axis=1)].reshape(-1,1)
        self.edge_org_id=[num for num in range(self.num_query) for _ in range(self.num_llms)]
        self.edge_des_id=list(range(self.edge_org_id[0],self.edge_org_id[0]+self.num_llms))*self.num_query

        self.mask_train =torch.zeros(len(self.edge_org_id))
        self.mask_train[train_idx]=1

        self.mask_validate = torch.zeros(len(self.edge_org_id))
        self.mask_validate[validate_idx] = 1

        self.mask_test = torch.zeros(len(self.edge_org_id))
        self.mask_test[test_idx] = 1


    def prepare_data_for_GNN(self):
        unique_index_list=list(range(0, len(self.data_df), self.num_llms))
        query_embedding_list_raw=self.data_df['query_embedding'].tolist()
        task_embedding_list_raw = self.data_df['task_description_embedding'].tolist()
        self.query_embedding_list= []
        self.task_embedding_list= []
        for inter in query_embedding_list_raw:
            inter=re.sub(r'\s+', ', ', inter.strip())
            try:
                inter=json.loads(inter)
            except:
                inter = inter.replace("[[,", "[[")
                inter = json.loads(inter)
            self.query_embedding_list.append(inter[0])

        for inter in task_embedding_list_raw:
            inter=re.sub(r'\s+', ', ', inter.strip())
            try:
                inter=json.loads(inter)
            except:
                inter = inter.replace("[[,", "[[")
                inter = json.loads(inter)
            self.task_embedding_list.append(inter[0])
        self.query_embedding_list=np.array(self.query_embedding_list)[unique_index_list]
        self.task_embedding_list = np.array(self.task_embedding_list)[unique_index_list]
        self.effect_list=np.array(self.data_df['effect'].tolist())
        self.cost_list=np.array(self.data_df['cost'].tolist())



    def train_GNN(self):

        self.data_for_GNN_train = self.form_data.formulation(task_id=self.task_embedding_list,
                                                             query_feature=self.query_embedding_list,
                                                             llm_feature=self.llm_description_embedding,
                                                             org_node=self.edge_org_id,
                                                             des_node=self.edge_des_id,
                                                             edge_feature=self.effect_list, edge_mask=self.mask_train,
                                                             label=self.label, combined_edge=self.combined_edge,
                                                             train_mask=self.mask_train, valide_mask=self.mask_validate,
                                                             test_mask=self.mask_test)
        self.data_for_GNN_validate = self.form_data.formulation(task_id=self.task_embedding_list,
                                                                query_feature=self.query_embedding_list,
                                                                llm_feature=self.llm_description_embedding,
                                                                org_node=self.edge_org_id,
                                                                des_node=self.edge_des_id,
                                                                edge_feature=self.effect_list,
                                                                edge_mask=self.mask_validate, label=self.label,
                                                                combined_edge=self.combined_edge,
                                                                train_mask=self.mask_train,
                                                                valide_mask=self.mask_validate,
                                                                test_mask=self.mask_test)

        self.data_for_test = self.form_data.formulation(task_id=self.task_embedding_list,
                                                        query_feature=self.query_embedding_list,
                                                        llm_feature=self.llm_description_embedding,
                                                        org_node=self.edge_org_id,
                                                        des_node=self.edge_des_id,
                                                        edge_feature=self.effect_list, edge_mask=self.mask_test,
                                                        label=self.label, combined_edge=self.combined_edge,
                                                        train_mask=self.mask_train, valide_mask=self.mask_validate,
                                                        test_mask=self.mask_test)
        self.GNN_predict.train_validate(data=self.data_for_GNN_train, data_validate=self.data_for_GNN_validate,data_for_test=self.data_for_test)

    def test_GNN(self):
        predicted_result = self.GNN_predict.test(data=self.data_for_test,model_path=self.config['model_path'])




if __name__ == "__main__":
    import wandb
    with open("configs/config.yaml", 'r', encoding='utf-8') as file:
        config = yaml.safe_load(file)
    wandb_key = config['wandb_key']
    wandb.login(key=wandb_key)
    wandb.init(project="graph_router")
    graph_router_prediction(router_data_path=config['saved_router_data_path'],llm_path=config['llm_description_path'],
                            llm_embedding_path=config['llm_embedding_path'],config=config,wandb=wandb)